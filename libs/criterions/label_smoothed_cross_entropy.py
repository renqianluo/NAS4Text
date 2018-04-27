#! /usr/bin/python
# -*- coding: utf-8 -*-

import math

from ..utils import common
from ..utils.debug_utils import get_valid_first_token_likelihood
from . import BaseCriterion, register_criterion

__author__ = 'fyabc'


@register_criterion('label_smoothed_cross_entropy')
class LabelSmoothedCrossEntropyCriterion(BaseCriterion):
    def __init__(self, hparams, src_dict, dst_dict):
        super().__init__(hparams, src_dict, dst_dict)
        self.eps = hparams.label_smoothing

    @staticmethod
    def add_args(parser):
        """Add criterion-specific arguments to the parser."""
        parser.add_argument('--label-smoothing', default=0., type=float, metavar='D',
                            help='epsilon for label smoothing, 0 means no label smoothing')

    def forward(self, model, sample, reduce=True):
        """Compute the loss for the given sample.

        Returns a tuple with three elements:
        1) the loss, as a Variable
        2) the sample size, which is used as the denominator for the gradient
        3) logging outputs to display while training
        """
        net_output = model(**sample['net_input'])
        lprobs = model.get_normalized_probs(net_output, log_probs=True)
        lprobs = lprobs.view(-1, lprobs.size(-1))
        target = model.get_targets(sample, net_output).view(-1, 1)
        # [DEBUG] {
        # get_valid_first_token_likelihood(model, model.get_targets(sample, net_output), net_output)
        # }
        non_pad_mask = target.ne(self.padding_idx)
        nll_loss = -lprobs.gather(dim=-1, index=target)[non_pad_mask]
        smooth_loss = -lprobs.sum(dim=-1, keepdim=True)[non_pad_mask]
        if reduce:
            nll_loss = nll_loss.sum()
            smooth_loss = smooth_loss.sum()
        eps_i = self.eps / lprobs.size(-1)
        loss = (1. - self.eps) * nll_loss + eps_i * smooth_loss

        sample_size = sample['target'].size(0) if self.hparams.sentence_avg else sample['ntokens']
        logging_output = {
            'loss': common.item(loss.data) if reduce else loss.data,
            'nll_loss': common.item(nll_loss.data) if reduce else loss.data,
            'ntokens': sample['ntokens'],
            'sample_size': sample_size,
        }
        return loss, sample_size, logging_output

    @staticmethod
    def aggregate_logging_outputs(logging_outputs):
        """Aggregate logging outputs from data parallel training."""
        ntokens = sum(log.get('ntokens', 0) for log in logging_outputs)
        sample_size = sum(log.get('sample_size', 0) for log in logging_outputs)
        return {
            'loss': sum(log.get('loss', 0) for log in logging_outputs) / sample_size / math.log(2),
            'nll_loss': sum(log.get('nll_loss', 0) for log in logging_outputs) / ntokens / math.log(2),
            'sample_size': sample_size,
        }