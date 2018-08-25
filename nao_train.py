#! /usr/bin/python
# -*- coding: utf-8 -*-

from contextlib import contextmanager
import copy
import itertools
import logging
import math
import os

import numpy as np
import torch as th
try:
    from tqdm import tqdm
except ImportError:
    tqdm = None

from libs.models.block_child_net import BlockChildNet
from libs.models.nao_child_net import NAOController
from libs.optimizers import build_optimizer
from libs.optimizers.lr_schedulers import build_lr_scheduler
from libs.criterions import build_criterion
from libs.utils import main_utils as mu
from libs.utils.meters import StopwatchMeter
from libs.utils.paths import get_data_path
from libs.utils.generator_utils import batch_bleu
from libs.utils import tokenizer
from libs.utils import nao_utils
from libs.utils import common
from libs.child_trainer import ChildTrainer
from libs.child_generator import ChildGenerator

__author__ = 'fyabc'


class NAOTrainer(ChildTrainer):
    # [NOTE]: Flags.
    ArchDist = False            # Train different arch on different GPUs.
    GenSortByLength = False     # Sort by length in generation.
    GenMaxlenB = 100            # Max length bias in generation. (less than normal generation to avoid oom)

    def __init__(self, hparams, criterion):
        super().__init__(hparams, None, criterion)
        # [NOTE]: Model is a "shared" model here.
        self.controller = NAOController(hparams).cuda()
        self.model = self.controller.shared_weights
        self.arch_pool = []
        self.arch_pool_prob = None
        self.eval_arch_pool = []
        self.performance_pool = []
        self._ref_tokens = None
        self._ref_dict = None

    def new_model(self, net_code, device=None, cuda=True):
        result = BlockChildNet(net_code, self.hparams, self.controller)
        if cuda:
            result = result.cuda(device)
        return result

    def init_arch_pool(self):
        if self.arch_pool:
            return
        num_seed_arch = self.hparams.num_seed_arch
        self.arch_pool_prob = None

        self.arch_pool = self.controller.generate_arch(num_seed_arch)

    def _sample_arch_from_pool(self):
        # TODO: Sample by model size. (Except shared parts (embedding, etc.)
        prob = self.arch_pool_prob
        if prob is None:
            pool_size = len(self.arch_pool)
            index = th.zeros([], dtype=th.int64).random_(0, pool_size).item()
        else:
            index = th.multinomial(prob).item()
        # print('$select index is:', index)
        return self.arch_pool[index]

    def train_children(self, datasets):
        logging.info('Training children, arch pool size = {}'.format(len(self.arch_pool)))
        eval_freq = self.hparams.child_eval_freq

        if self.single_gpu:
            arch = self._sample_arch_from_pool()
            child = self.new_model(arch)

            # Train the child model for some epochs.
            with self.child_env(child):
                logging.info('Number of child model parameters: {}'.format(child.num_parameters()))
                print('Architecture:', arch.blocks['enc1'], arch.blocks['dec1'], sep='\n\t')

                self._init_meters()
                for epoch in range(1, eval_freq + 1):
                    mu.train(self.hparams, self, datasets, epoch, 0)

            return

        if self.ArchDist:
            # # Random sample one arch per card to train.
            # for device in range(self.num_gpus):
            #     arch = self._sample_arch_from_pool()
            #     child = self.new_model(arch, device)
            #
            #     # TODO: How to distributed training on all GPU cards async?
            raise NotImplementedError('Arch dist multi-gpu training not supported yet')
        else:
            raise NotImplementedError('Non-arch dist multi-gpu training not supported yet')

    def eval_children(self, datasets, compute_loss=False):
        """Eval all arches in the pool."""

        self._get_ref_tokens(datasets)

        # Prepare the generator.
        generator = ChildGenerator(
            self.hparams, datasets, [self.model], subset='dev', quiet=True, output_file=None,
            use_task_maxlen=False, maxlen_a=0, maxlen_b=self.GenMaxlenB, max_tokens=None,
            max_sentences=self.hparams.child_eval_batch_size, beam=5, lenpen=1.2,
        )
        itr = generator.get_input_iter(sort_by_length=self.GenSortByLength)
        itr_chain = [itr]
        # [NOTE]: Make sure that each arch can process one batch. Use multiple iterators.
        repeat_number = math.ceil(len(self.arch_pool) / len(itr))
        for _ in range(1, repeat_number):
            itr_chain.append(generator.get_input_iter(sort_by_length=self.GenSortByLength))
        whole_gen_itr = itertools.chain(*itr_chain)

        arch_itr = self.arch_pool
        if tqdm is not None:
            arch_itr = tqdm(arch_itr)

        val_loss_list = []
        val_acc_list = []
        valid_time = StopwatchMeter()
        for arch, sample in zip(arch_itr, whole_gen_itr):
            child = self.new_model(arch, cuda=False)

            if compute_loss:
                with self.child_env(child, train=False):
                    val_loss = mu.validate(self.hparams, self, datasets, 'dev', self.hparams.child_eval_freq)
                val_loss_list.append(val_loss)

            generator.models = [child]
            generator.cuda()

            # FIXME: Use beam 5 decoding here.
            translation = generator.decoding_one_batch(sample)
            val_bleu = batch_bleu(generator, sample['id'], translation, self._ref_tokens, self._ref_dict)
            val_acc_list.append(val_bleu)

        valid_time.stop()
        logging.info('''\
Evaluation on valid data: totally validated {} architectures
Metrics: loss={}, valid_accuracy={:<8.6f}, secs={:<10.2f}'''.format(
            len(self.arch_pool), ':<6f'.format(np.mean(val_loss_list)) if compute_loss else '[NotComputed]',
            np.mean(val_acc_list), valid_time.sum,
        ))
        return val_acc_list

    @contextmanager
    def child_env(self, model, train=True):
        logging.info('Creating child {} environment'.format('train' if train else 'valid'))
        if train:
            optimizer = build_optimizer(self.hparams, model.parameters())
            lr_scheduler = build_lr_scheduler(self.hparams, optimizer)
            logging.info('Child optimizer: {}'.format(optimizer.__class__.__name__))
            logging.info('Child LR Scheduler: {}'.format(lr_scheduler.__class__.__name__))

        old_model = self.model
        self.model = model
        if train:
            old_optimizer = self.optimizer
            old_lr_scheduler = self.lr_scheduler
            self.optimizer = optimizer
            self.lr_scheduler = lr_scheduler

        try:
            yield
        finally:
            self.model = old_model
            if train:
                self.optimizer = old_optimizer
                self.lr_scheduler = old_lr_scheduler
            logging.info('Trainer restored')

    def _get_ref_tokens(self, datasets):
        if self._ref_tokens is not None:
            return
        dataset_dir = get_data_path(self.hparams)
        dev_filename = datasets.task.get_filename('dev', is_src_lang=False)
        dev_orig_filename = dev_filename + '.orig'  # FIXME: Hard-coding here.

        # [NOTE]: Reuse target dictionary.
        from copy import deepcopy
        dict_ = self._ref_dict = deepcopy(datasets.target_dict)
        # dict_ = self._ref_dict = dictionary.Dictionary(None, datasets.task, mode='empty')

        self._ref_tokens = []
        with open(os.path.join(dataset_dir, dev_orig_filename), 'r', encoding='utf-8') as ref_f:
            for line in ref_f:
                self._ref_tokens.append(tokenizer.Tokenizer.tokenize(line, dict_, tensor_type=th.IntTensor))

    def _parse_arch_to_seq(self, arch):
        return self.controller.parse_arch_to_seq(arch)

    def _normalized_perf(self, perf_list):
        max_val, min_val = np.max(perf_list), np.min(perf_list)
        return [(v - min_val) / (max_val - min_val) for v in perf_list]

    def controller_train_step(self, old_arches, old_arches_perf):
        logging.info('Training Encoder-Predictor-Decoder')

        arch_seqs = [self._parse_arch_to_seq(arch) for arch in old_arches]
        perf = self._normalized_perf(old_arches_perf)
        ctrl_dataloader = nao_utils.make_ctrl_dataloader(
            arch_seqs, perf, batch_size=self.hparams.ctrl_batch_size,
            shuffle=False, sos_id=self.controller.epd.sos_id)

        epochs = range(1, self.hparams.ctrl_train_epochs + 1)
        if tqdm is not None:
            epochs = tqdm(list(epochs))

        for epoch in epochs:
            # TODO: Train the epd.
            for step, sample in enumerate(ctrl_dataloader):
                sample = nao_utils.prepare_ctrl_sample(sample, evaluation=False)
                # FIXME: Use ParallelModel here?
                predict_value, logits, arch = self.controller.epd(sample['encoder_input'], sample['decoder_input'])

    def controller_generate_step(self, old_arches):
        old_arches = old_arches[:self.hparams.num_remain_top]
        new_arches = []
        predict_lambda = 0
        topk_arches = [self._parse_arch_to_seq(arch) for arch in old_arches[:self.hparams.num_pred_top]]

        while len(new_arches) + len(old_arches) < self.hparams.num_seed_arch:
            # TODO: Generate new arches.
            predict_lambda += 1
            pass


def nao_search_main(hparams):

    components = mu.main_entry(hparams, train=True, net_code='nao')
    datasets = components['datasets']

    logging.info('Building model')
    criterion = build_criterion(hparams, datasets.source_dict, datasets.target_dict)
    trainer = NAOTrainer(hparams, criterion)
    model = trainer.get_model()
    mu.logging_model_criterion(model, criterion)
    mu.logging_training_stats(hparams)

    max_ctrl_step = hparams.max_ctrl_step or math.inf
    ctrl_step = 1
    train_meter = StopwatchMeter()
    train_meter.start()
    while ctrl_step <= max_ctrl_step:
        logging.info('Training step {}'.format(ctrl_step))
        trainer.set_seed(ctrl_step)

        # Train child model.
        trainer.init_arch_pool()

        trainer.train_children(datasets)

        # Evaluate seed arches.
        valid_acc_list = trainer.eval_children(datasets, compute_loss=False)

        # Output arches and evaluated error rate.
        old_arches = trainer.arch_pool
        # Error rate list.
        old_arches_perf = [1.0 - i for i in valid_acc_list]

        # Sort old arches.
        old_arches_sorted_indices = np.argsort(old_arches_perf)
        old_arches = [old_arches[i] for i in old_arches_sorted_indices]
        old_arches_perf = [old_arches_perf[i] for i in old_arches_sorted_indices]

        # Save old arches in order.
        nao_utils.save_arches(hparams, ctrl_step, old_arches, old_arches_perf)

        # Train encoder-predictor-decoder.
        trainer.controller_train_step(old_arches, old_arches_perf)

        # Generate new arches.
        trainer.controller_generate_step(old_arches)

        ctrl_step += 1
    train_meter.stop()
    logging.info('Training done in {:.1f} seconds'.format(train_meter.sum))


def main(args=None):
    hparams = nao_utils.get_nao_search_args(args)
    nao_search_main(hparams)


if __name__ == '__main__':
    main()
