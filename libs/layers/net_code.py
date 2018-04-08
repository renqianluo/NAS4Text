#! /usr/bin/python
# -*- coding: utf-8 -*-

"""Convert net code between different formats.

Standard format:
[   # Network
    [   # Layer 0
        LAYER_TYPE,
        LAYER_HPARAMS1,
        LAYER_HPARAMS2,
        ...
    ],
    [   # Layer 1
        ...
    ],
    ...
]
"""

__author__ = 'fyabc'


class NetCodeEnum:
    # Layer types.
    LSTM = 0
    Convolutional = 1
    Attention = 2

    # Recurrent hyperparameters.

    # Convolutional hyperparameters.

    # Attention hyperparameters.


def dump_json(net_code):
    pass


def load_json(fp):
    pass