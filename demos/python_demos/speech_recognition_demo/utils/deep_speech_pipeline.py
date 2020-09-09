#
# Copyright (C) 2019-2020 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
#
# This file is based in part on deepspeech_openvino_0.5.py by Feng Yen-Chang at
# https://github.com/opencv/open_model_zoo/pull/419, commit 529805d011d9b405f142b2b40f4d202bd403a4f1 on Sep 19, 2019.
#
import os.path

import numpy as np
from openvino.inference_engine import IENetwork, IECore

import utils.alphabet as alphabet_module
from utils.audio_features import audio_spectrogram, mfcc
from utils.ctcnumpy_beam_search_decoder import CtcnumpyBeamSearchDecoder


class DeepSpeechPipeline:
    def __init__(self, model, model_bin=None, lm=None, alphabet=None,
            beam_width=500, alpha=0.75, beta=1.85,
            ie=None, device='CPU', ie_extensions=[]):
        """
            Args:
        model (str), filename of IE IR .xml file of the network
        model_bin (str), filename of IE IR .xml file of the network (deafult (None) is the same as :model:, but
            with extension replaced with .bin)
        lm (str), filename of LM (language model)
        alphabet (None or str or list(str)), alphabet matching the model (default None):
            None = [' ', 26 English letters, apostrophe];
            str = filename of a text file with the alphabet (expluding separator=blank symbol)
            list(str) = the alphabet itself (expluding separator=blank symbol)
        beam_width (int), the number of prefix candidates to retain during decoding in beam search (default 500)
        alpha (float), LM weight relative to audio model (default 0.75)
        beta (float), word insertion bonus to counteract LM's tendency to prefer fewer words (default 1.85)
        ie (IECore or None), IECore object to run NN inference with.  Default is to use ie_core_singleton module.
            (default None)
        device (str), inference device for IE, passed here to 1. set default device, and 2. check supported node types
            in the model load; None = do not check (default 'CPU')
        ie_extensions (list(tuple(str,str))), list of IE extensions to load, each extension is defined by a pair
            (device, filename). Records with filename=None are ignored.  (default [])
        """
        # model parameters
        self.num_mfcc_dct_coefs = 26
        self.num_context_frames = 19
        self.num_batch_frames = 16
        self.model_sample_rate = 16000
        self.frame_window_size_seconds = 32e-3
        self.frame_stride_seconds = 20e-3

        self.beam_width = beam_width
        if alphabet is None:
            self.alphabet = alphabet_module.get_default_alphabet()
        elif isinstance(alphabet, str):
            self.alphabet = alphabet_module.load_alphabet(alphabet)  # shall not include <blank> token
        else:
            self.alphabet = alphabet

        self.net = self.exec_net = None
        self.default_device = device

        self.ie = ie if ie is not None else IECore()
        self._load_net(model, model_bin_fname=model_bin, device=device, ie_extensions=ie_extensions)

        self.decoder = CtcnumpyBeamSearchDecoder(self.alphabet, self.beam_width,
            scorer_lm_fname=lm, alpha=alpha, beta=beta)

        if device is not None:
            self.activate_model(device)


    def _load_net(self, model_xml_fname, model_bin_fname=None, ie_extensions=[], device='CPU', device_config=None):
        """
        Load IE IR of the network,  and optionally check it for supported node types by the target device.

        model_xml_fname (str)
        model_bin_fname (str or None)
        ie_extensions (list of tuple(str,str)), list of plugins to load, each element is a pair
            (device_name, plugin_filename) (default [])
        device (str or None), check supported node types with this device; None = do not check (default 'CPU')
        device_config
        """
        if model_bin_fname is None:
            model_bin_fname = os.path.basename(model_xml_fname).rsplit('.', 1)[0] + '.bin'
            model_bin_fname = os.path.join(os.path.dirname(model_xml_fname), model_bin_fname)

        # Plugin initialization for specified device and load extensions library if specified
        for extension_device, extension_fname in ie_extensions:
            if extension_fname is None:
                continue
            self.ie.add_extension(extension_path=extension_fname, device_name=extension_device)

        # Read IR
        self.net = self.ie.read_network(model=model_xml_fname, weights=model_bin_fname)

        if device is not None:
            self._check_ir_nodes(device, device_config)

    def _check_ir_nodes(self, device, device_config):
        # Check NN nodes
        device_supported_layers = self.ie.query_network(self.net, device, device_config)
        net_unsupported_layers = [l for l in self.net.layers.keys() if l not in device_supported_layers]
        if len(net_unsupported_layers) > 0:
            raise RuntimeError(
                ("Following layers are not supported by the plugin for specified device {}: {}.  " +
                "Please try to specify IE extension library path with --cpu_extension command line argument").
                format(device, ', '.join(net_unsupported_layers))
            )

    def activate_model(self, device):
        if self.exec_net is not None:
            return  # Assuming self.net didn't change
        # Loading model to the plugin
        self.exec_net = self.ie.load_network(network=self.net, device_name=device)

    def recognize_audio(self, audio, sampling_rate):
        mfcc_features = self.extract_mfcc(audio, sampling_rate)
        probs = self.extract_per_frame_probs(mfcc_features)
        del mfcc_features
        transcription = self.decode_probs(probs)
        return transcription

    def extract_mfcc(self, audio, sampling_rate):
        # Audio feature extraction
        if abs(sampling_rate - self.model_sample_rate) > self.model_sample_rate * 0.1  or  (audio.shape + (1,))[1] != 1:
            raise ValueError("Input audio file should be {} kHz mono".format(self.model_sample_rate/1e3))
        if np.issubdtype(audio.dtype, np.integer):
            audio = audio/np.float32(32768) # normalize to -1 to 1, int16 to float32
        audio = audio.reshape(-1, 1)
        spectrogram = audio_spectrogram(
            audio,
            sampling_rate * self.frame_window_size_seconds,
            sampling_rate * self.frame_stride_seconds,
            True,
        )
        features = mfcc(spectrogram.reshape(1, spectrogram.shape[0], -1), sampling_rate, self.num_mfcc_dct_coefs)
        return features

    def extract_per_frame_probs(self, mfcc_features, state=None, return_state=False, wrap_iterator=lambda x:x):
        assert self.exec_net is not None, "Need to call mds.activate(device) method before mds.stt(...)"

        padding = np.zeros((self.num_context_frames // 2, self.num_mfcc_dct_coefs), dtype=mfcc_features.dtype)
        mfcc_features = np.concatenate((padding, mfcc_features, padding))  # TODO: replace with np.pad

        num_strides = len(mfcc_features) - self.num_context_frames + 1
        # Create a view into the array with overlapping strides to simulate convolution with FC
        mfcc_features = np.lib.stride_tricks.as_strided(  # TODO: replace with conv1d
            mfcc_features,
            (num_strides, self.num_context_frames, self.num_mfcc_dct_coefs),
            (mfcc_features.strides[0], mfcc_features.strides[0], mfcc_features.strides[1]),
            writeable = False,
        )

        if state is None:
            state_h = np.zeros((1, 2048))
            state_c = np.zeros((1, 2048))
        else:
            state_h, state_c = state

        probs = []
        for i in wrap_iterator(range(0, mfcc_features.shape[0], self.num_batch_frames)):
            chunk = mfcc_features[i:i + self.num_batch_frames]

            if len(chunk) < self.num_batch_frames:
                chunk = np.pad(
                    chunk,
                    (
                        (0, self.num_batch_frames - len(chunk)),
                        (0, 0),
                        (0, 0),
                    ),
                    mode = 'constant',
                    constant_values = 0,
                )

            res = self.exec_net.infer(inputs={
                'previous_state_c': state_c,
                'previous_state_h': state_h,
                'input_node': [chunk],
            })
            probs.append(res['logits'].squeeze(1))  # they are actually probabilities after softmax, not logits
            state_h = res['cudnn_lstm/rnn/multi_rnn_cell/cell_0/cudnn_compatible_lstm_cell/BlockLSTM/TensorIterator.1']
            state_c = res['cudnn_lstm/rnn/multi_rnn_cell/cell_0/cudnn_compatible_lstm_cell/BlockLSTM/TensorIterator.2']
        probs = np.concatenate(probs)

        if not return_state:
            return probs
        else:
            return probs, (state_h, state_c)

    def decode_probs(self, probs):
        """
        Return list of pairs (-log_score, text) in order of decreasing (audio+LM) score
        """
        return self.decoder.decode(probs)
