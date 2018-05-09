#! /usr/bin/python
# -*- coding: utf-8 -*-

"""Convert net code between different formats.

Standard format:
[   # Network
    [   # Encoder
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
    ],
    [   # Decoder, same as encoder
        ...
    ]
]

LAYER_TYPE and LAYER_HPARAMS are all integers.
They are indices of candidate lists defined by hparams.

Meanings of LAYER_HPARAMS can be seen in lstm.py, cnn.py and attention.py.

Example:
    # Assume that all search spaces are 'normal'.
    [   # Network
        [   # Encoder
            [1, 2, 1, 0, 1, 0], # Encoder layer 0
            [1, 2, 1, 0, 1, 0], # Encoder layer 1
            [1, 2, 1, 0, 1, 0], # Encoder layer 2
            [1, 2, 1, 0, 1, 0]  # Encoder layer 3
        ],
        [   # Decoder
            [1, 2, 1, 0, 1, 0], # Decoder layer 0
            [1, 2, 1, 0, 1, 0], # Decoder layer 1
            [1, 2, 1, 0, 1, 0]  # Decoder layer 2
        ]
    ]

    => For encoder layer 0:
    code = [1, 2, 1, 0, 1, 0]
    code[0] == 1: 1 means 'Convolutional'

    => Then see the layer code format and 'normal' convolutional search space: (in cnn.py)
    ```python
    # Layer code:
    # [CNN, OutChannels, KernelSize, Stride, ..., Preprocessors, Postprocessors]

    class ConvSpaceBase:
        OutChannels = [8, 16, 32, 64]
        KernelSizes = [1, 3, 5, 7]
        Strides = [1, 2, 3]

        Preprocessors = PPPSpace.Preprocessors
        Postprocessors = PPPSpace.Postprocessors
    ```

    => So,
    code[1] == 2: 2 means OutChannels[2] -> 32
    code[2] == 1: 1 means KernelSizes[1] -> 3
    code[3] == 0: 0 means Stride[0] -> 1
    code[4] == 1: 1 means Preprocessors[1] -> Dropout   (see in ppp.py)
    code[5] == 0: 0 means Postprocessors[0] -> None     (see in ppp.py)

    => So the result layer is (you can found it in net_code_examples/fairseq_d.json):
    (layer_0): EncoderConvLayer(
        (preprocessors): ModuleList(
            (0): Dropout(p=0.1)
        )
        (postprocessors): ModuleList(
        )
        (conv): Conv1d (256, 512, kernel_size=(3,), stride=(1,))
    )
"""

import json
import os
import pickle
import re

__author__ = 'fyabc'


class NetCodeEnum:
    # Layer types.
    LSTM = 0
    Convolutional = 1
    Attention = 2


def dump_json(net_code, fp):
    json.dump(net_code, fp)


def load_json(fp):
    """Load net code from JSON file, remove line comments."""
    return json.loads(''.join(re.sub(r'//.*\n', '\n', _line) for _line in fp))


def load_pickle(fp):
    return pickle.load(fp)


def check_correctness(code):
    pass


def get_net_code(hparams):
    # TODO: Other format, correctness check, etc.
    with open(hparams.net_code_file, 'r') as f:
        ext = os.path.splitext(f.name)[1]
        if ext == '.json':
            code = load_json(f)
        elif ext == '.pkl':
            code = load_pickle(f)
        else:
            raise ValueError('Does not support this net code file format now')

        check_correctness(code)
        return code
