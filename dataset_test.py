#! /usr/bin/python
# -*- coding: utf-8 -*-

import logging

from libs.utils.args import get_args
from libs.utils.data_processing import LanguageDatasets

__author__ = 'fyabc'


def main(args=None):
    logging.basicConfig(
        format='{levelname}:{message}',
        level=logging.DEBUG,
        style='{',
    )

    hparams = get_args(args)

    datasets = LanguageDatasets(hparams.task)

    print(len(datasets.source_dict))
    print(len(datasets.target_dict))


if __name__ == '__main__':
    main(['-T', 'de_en_iwslt'])