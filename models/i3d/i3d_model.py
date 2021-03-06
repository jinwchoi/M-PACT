" I3D MODEL WITH FROZEN BATCH-NORM WEIGHTS IMPLEMENTATION FOR USE WITH TENSORFLOW "

import os
import time
import sys
sys.path.append('../..')

import tensorflow as tf
import numpy      as np

from models.models_abstract import Abstract_Model_Class
from utils.layers_utils     import *

from default_preprocessing import preprocess

class I3D(Abstract_Model_Class):

    def __init__(self, **kwargs):
        """
        Args:
            Pass all arguments on to parent class, you may not add additional arguments without modifying abstract_model_class.py     and Models.py. Enter any additional initialization functionality here if desired.
        """
        super(I3D, self).__init__(**kwargs)


    def _unit_3d(self, layer_numbers, input_layer, kernel_size=(1,1,1,1), stride=(1,1,1), activation_fn=tf.nn.relu, use_batch_norm=True, use_bias=False, is_training=True, name='unit_3d', freeze=False):
        """
        Args:
            :layer_numbers:   List detailing the connecting layer indices
            :input_layer:     Input layer to the conv_block
            :kernel_size:     List detailing the height, width  and temporal dimension of the kernel
            :strides:         Integer value for stride between filters (Height, Width and Temporal width)
            :activation_fn:   Activation function to be applied at the end of 3d convolution
            :use_batch_norm:  Boolean indicating the use of batch normalization
            :use_bias:        Boolean indication the use of bias
            :name:            Name of 3d convolution unit

        Return:
            :layers:        Stack of layers
        """

        # BIAS IS NOT USED BUT OUR LAYER UTILS DOES NOT OFFER THE OPTION TO AVOID BIAS!!

        layers = {}

        layers[layer_numbers[0]] = conv3d_layer(input_tensor = input_layer, filter_dims = kernel_size, name = 'RGB/inception_i3d/' + name + '/conv_3d', stride_dims = stride, non_linear_fn = None, use_bias=use_bias, trainable=freeze)

        if use_batch_norm:
            layers[layer_numbers[1]] = batch_normalization(layers[layer_numbers[0]], training = is_training, name = 'RGB/inception_i3d/' + name + '/batch_norm', trainable=freeze)

            if activation_fn is not None:
                layers[layer_numbers[2]] = activation_fn(layers[layer_numbers[1]])

            # END IF

        else:
            if activation_fn is not None:
                layers[layer_numbers[1]] = activation_fn(layers[layer_numbers[0]])

            # END IF

        # END IF

        return layers


    def inference(self, inputs, is_training, input_dims, output_dims, seq_length, scope, dropout_rate = 0.7, return_layer=['logits'], weight_decay=0.0):
        """
        Args:
            :inputs:       Input to model of shape [Frames x Height x Width x Channels]
            :is_training:  Boolean variable indicating phase (TRAIN OR TEST)
            :input_dims:   Length of input sequence
            :output_dims:  Integer indicating total number of classes in final prediction
            :seq_length:   Length of output sequence from LSTM
            :scope:        Scope name for current model instance
            :dropout_rate: Value indicating proability of keep inputs
            :return_layer: List of strings matching name of a layer in current model
            :weight_decay: Double value of weight decay

        Return:
            :layers[return_layer]: The requested layer's output tensor
        """

        ############################################################################
        #                Creating ResNet50 + LSTM Network Layers                   #
        ############################################################################

        if self.verbose:
            print('Generating I3D network layers')

        # END IF


        with tf.name_scope(scope, 'i3d', [inputs]):

            layers = {}

            layers.update(self._unit_3d(layer_numbers=['1','2','3'], input_layer=inputs, kernel_size=[7,7,7,64], stride=[2,2,2], name='Conv3d_1a_7x7', is_training=False))

            layers['4'] = max_pool3d_layer(layers['3'], filter_dims=[1,1,3,3,1], stride_dims=[1,1,2,2,1], padding='SAME', name='RGB/inception_i3d/MaxPool3d_2a_3x3')

            layers.update(self._unit_3d(layer_numbers=['5','6','7'], input_layer=layers['4'], kernel_size=[1,1,1,64], name='Conv3d_2b_1x1', is_training=False))

            layers.update(self._unit_3d(layer_numbers=['8','9','10'], input_layer=layers['7'], kernel_size=[3,3,3,192], name='Conv3d_2c_3x3', is_training=False))

            layers['11_inp'] = max_pool3d_layer(layers['10'], filter_dims=[1,1,3,3,1], stride_dims=[1,1,2,2,1], padding='SAME', name='RGB/inception_i3d/MaxPool3d_3a_3x3')

            #### Mixed_3b ####

            layers.update(self._unit_3d(layer_numbers=['11','12','13'], input_layer=layers['11_inp'], kernel_size=[1,1,1,64], name='Mixed_3b/Branch_0/Conv3d_0a_1x1', is_training=False))

            layers.update(self._unit_3d(layer_numbers=['14','15','16'], input_layer=layers['11_inp'], kernel_size=[1,1,1,96], name='Mixed_3b/Branch_1/Conv3d_0a_1x1', is_training=False))

            layers.update(self._unit_3d(layer_numbers=['17','18','19'], input_layer=layers['16'], kernel_size=[3,3,3,128], name='Mixed_3b/Branch_1/Conv3d_0b_3x3', is_training=False))

            layers.update(self._unit_3d(layer_numbers=['20','21','22'], input_layer=layers['11_inp'], kernel_size=[1,1,1,16], name='Mixed_3b/Branch_2/Conv3d_0a_1x1', is_training=False))

            layers.update(self._unit_3d(layer_numbers=['23','24','24'], input_layer=layers['22'], kernel_size=[3,3,3,32], name='Mixed_3b/Branch_2/Conv3d_0b_3x3', is_training=False))

            layers['25'] = max_pool3d_layer(layers['11_inp'], filter_dims=[1,3,3,3,1], stride_dims=[1,1,1,1,1], padding='SAME', name='RGB/inception_i3d/Mixed_3b/Branch_3/MaxPool3d_0a_3x3')

            layers.update(self._unit_3d(layer_numbers=['26','27','28'], input_layer=layers['25'], kernel_size=[1,1,1,32], name='Mixed_3b/Branch_3/Conv3d_0b_1x1', is_training=False))

            layers['29'] = tf.concat([layers['13'], layers['19'], layers['24'], layers['28']], 4)

            #### END OF MIXED_3b ####

            #### Mixed_3c ####

            layers.update(self._unit_3d(layer_numbers=['30','31','32'], input_layer=layers['29'], kernel_size=[1,1,1,128], name='Mixed_3c/Branch_0/Conv3d_0a_1x1', is_training=False))

            layers.update(self._unit_3d(layer_numbers=['33','34','35'], input_layer=layers['29'], kernel_size=[1,1,1,128], name='Mixed_3c/Branch_1/Conv3d_0a_1x1', is_training=False))

            layers.update(self._unit_3d(layer_numbers=['36','37','38'], input_layer=layers['35'], kernel_size=[3,3,3,192], name='Mixed_3c/Branch_1/Conv3d_0b_3x3', is_training=False))

            layers.update(self._unit_3d(layer_numbers=['39','40','41'], input_layer=layers['29'], kernel_size=[1,1,1,32], name='Mixed_3c/Branch_2/Conv3d_0a_1x1', is_training=False))

            layers.update(self._unit_3d(layer_numbers=['42','43','44'], input_layer=layers['41'], kernel_size=[3,3,3,96], name='Mixed_3c/Branch_2/Conv3d_0b_3x3', is_training=False))

            layers['45'] = max_pool3d_layer(layers['29'], filter_dims=[1,3,3,3,1], stride_dims=[1,1,1,1,1], padding='SAME', name='RGB/inception_i3d/Mixed_3c/Branch_3/MaxPool3d_0a_3x3')

            layers.update(self._unit_3d(layer_numbers=['46','47','48'], input_layer=layers['45'], kernel_size=[1,1,1,64], name='Mixed_3c/Branch_3/Conv3d_0b_1x1', is_training=False))

            layers['49'] = tf.concat([layers['32'], layers['38'], layers['44'], layers['48']], 4)

            #### END OF MIXED_3c ####

            layers['50'] = max_pool3d_layer(layers['49'], filter_dims=[1,3,3,3,1], stride_dims=[1,2,2,2,1], padding='SAME', name='RGB/inception_i3d/MaxPool3d_4a_3x3')

            #### Mixed_4b ####

            layers.update(self._unit_3d(layer_numbers=['51','52','53'], input_layer=layers['50'], kernel_size=[1,1,1,192], name='Mixed_4b/Branch_0/Conv3d_0a_1x1', is_training=False))

            layers.update(self._unit_3d(layer_numbers=['54','55','56'], input_layer=layers['50'], kernel_size=[1,1,1,96], name='Mixed_4b/Branch_1/Conv3d_0a_1x1', is_training=False))

            layers.update(self._unit_3d(layer_numbers=['57','58','59'], input_layer=layers['56'], kernel_size=[3,3,3,208], name='Mixed_4b/Branch_1/Conv3d_0b_3x3', is_training=False))

            layers.update(self._unit_3d(layer_numbers=['60','61','62'], input_layer=layers['50'], kernel_size=[1,1,1,16], name='Mixed_4b/Branch_2/Conv3d_0a_1x1', is_training=False))

            layers.update(self._unit_3d(layer_numbers=['63','64','65'], input_layer=layers['62'], kernel_size=[3,3,3,48], name='Mixed_4b/Branch_2/Conv3d_0b_3x3', is_training=False))

            layers['66'] = max_pool3d_layer(layers['50'], filter_dims=[1,3,3,3,1], stride_dims=[1,1,1,1,1], padding='SAME', name='RGB/inception_i3d/Mixed_4b/Branch_3/MaxPool3d_0a_3x3')

            layers.update(self._unit_3d(layer_numbers=['67','68','69'], input_layer=layers['66'], kernel_size=[1,1,1,64], name='Mixed_4b/Branch_3/Conv3d_0b_1x1', is_training=False))

            layers['70'] = tf.concat([layers['53'], layers['59'], layers['65'], layers['69']], 4)

            #### END OF MIXED_4b ####

            #### Mixed_4c ####

            layers.update(self._unit_3d(layer_numbers=['71','72','73'], input_layer=layers['70'], kernel_size=[1,1,1,160], name='Mixed_4c/Branch_0/Conv3d_0a_1x1', is_training=False))

            layers.update(self._unit_3d(layer_numbers=['74','75','76'], input_layer=layers['70'], kernel_size=[1,1,1,112], name='Mixed_4c/Branch_1/Conv3d_0a_1x1', is_training=False))

            layers.update(self._unit_3d(layer_numbers=['77','78','79'], input_layer=layers['76'], kernel_size=[3,3,3,224], name='Mixed_4c/Branch_1/Conv3d_0b_3x3', is_training=False))

            layers.update(self._unit_3d(layer_numbers=['80','81','82'], input_layer=layers['70'], kernel_size=[1,1,1,24], name='Mixed_4c/Branch_2/Conv3d_0a_1x1', is_training=False))

            layers.update(self._unit_3d(layer_numbers=['83','84','85'], input_layer=layers['82'], kernel_size=[3,3,3,64], name='Mixed_4c/Branch_2/Conv3d_0b_3x3', is_training=False))

            layers['86'] = max_pool3d_layer(layers['70'], filter_dims=[1,3,3,3,1], stride_dims=[1,1,1,1,1], padding='SAME', name='RGB/inception_i3d/Mixed_4c/Branch_3/MaxPool3d_0a_3x3')

            layers.update(self._unit_3d(layer_numbers=['87','88','89'], input_layer=layers['86'], kernel_size=[1,1,1,64], name='Mixed_4c/Branch_3/Conv3d_0b_1x1', is_training=False))

            layers['90'] = tf.concat([layers['73'], layers['79'], layers['85'], layers['89']], 4)

            #### END OF MIXED_4c ####

            #### Mixed_4d ####

            #with tf.variable_scope('Mixed_4d'):
            #    with tf.variable_scope('Branch_0'):
            layers.update(self._unit_3d(layer_numbers=['91','92','93'], input_layer=layers['90'], kernel_size=[1,1,1,128], name='Mixed_4d/Branch_0/Conv3d_0a_1x1', is_training=False))

                # END WITH

                #with tf.variable_scope('Branch_1'):
            layers.update(self._unit_3d(layer_numbers=['94','95','96'], input_layer=layers['90'], kernel_size=[1,1,1,128], name='Mixed_4d/Branch_1/Conv3d_0a_1x1', is_training=False))

            layers.update(self._unit_3d(layer_numbers=['97','98','99'], input_layer=layers['96'], kernel_size=[3,3,3,256], name='Mixed_4d/Branch_1/Conv3d_0b_3x3', is_training=False))

                # END WITH

                #with tf.variable_scope('Branch_2'):
            layers.update(self._unit_3d(layer_numbers=['100','101','102'], input_layer=layers['90'], kernel_size=[1,1,1,24], name='Mixed_4d/Branch_2/Conv3d_0a_1x1', is_training=False))

            layers.update(self._unit_3d(layer_numbers=['103','104','105'], input_layer=layers['102'], kernel_size=[3,3,3,64], name='Mixed_4d/Branch_2/Conv3d_0b_3x3', is_training=False))

                # END WITH

                #with tf.variable_scope('Branch_3'):
            layers['106'] = max_pool3d_layer(layers['90'], filter_dims=[1,3,3,3,1], stride_dims=[1,1,1,1,1], padding='SAME', name='RGB/inception_i3d/Mixed_4d/Branch_3/MaxPool3d_0a_3x3')

            layers.update(self._unit_3d(layer_numbers=['107','108','109'], input_layer=layers['106'], kernel_size=[1,1,1,64], name='Mixed_4d/Branch_3/Conv3d_0b_1x1', is_training=False))

                # END WITH

            layers['110'] = tf.concat([layers['93'], layers['99'], layers['105'], layers['109']], 4)

            # END WITH

            #### END OF MIXED_4d ####

            #### Mixed_4e ####

            layers.update(self._unit_3d(layer_numbers=['111','112','113'], input_layer=layers['110'], kernel_size=[1,1,1,112], name='Mixed_4e/Branch_0/Conv3d_0a_1x1', is_training=False))

            layers.update(self._unit_3d(layer_numbers=['114','115','116'], input_layer=layers['110'], kernel_size=[1,1,1,144], name='Mixed_4e/Branch_1/Conv3d_0a_1x1', is_training=False))

            layers.update(self._unit_3d(layer_numbers=['117','118','119'], input_layer=layers['116'], kernel_size=[3,3,3,288], name='Mixed_4e/Branch_1/Conv3d_0b_3x3', is_training=False))

            layers.update(self._unit_3d(layer_numbers=['120','121','122'], input_layer=layers['110'], kernel_size=[1,1,1,32], name='Mixed_4e/Branch_2/Conv3d_0a_1x1', is_training=False))

            layers.update(self._unit_3d(layer_numbers=['123','124','125'], input_layer=layers['122'], kernel_size=[3,3,3,64], name='Mixed_4e/Branch_2/Conv3d_0b_3x3', is_training=False))

            layers['126'] = max_pool3d_layer(layers['110'], filter_dims=[1,3,3,3,1], stride_dims=[1,1,1,1,1], padding='SAME', name='RGB/inception_i3d/Mixed_4e/Branch_3/MaxPool3d_0a_3x3')

            layers.update(self._unit_3d(layer_numbers=['127','128','129'], input_layer=layers['126'], kernel_size=[1,1,1,64], name='Mixed_4e/Branch_3/Conv3d_0b_1x1', is_training=False))

            layers['130'] = tf.concat([layers['113'], layers['119'], layers['125'], layers['129']], 4)

            #### END OF MIXED_4e ####

            #### Mixed_4f ####

            layers.update(self._unit_3d(layer_numbers=['131','132','133'], input_layer=layers['130'], kernel_size=[1,1,1,256], name='Mixed_4f/Branch_0/Conv3d_0a_1x1', is_training=False))

            layers.update(self._unit_3d(layer_numbers=['134','135','136'], input_layer=layers['130'], kernel_size=[1,1,1,160], name='Mixed_4f/Branch_1/Conv3d_0a_1x1', is_training=False))

            layers.update(self._unit_3d(layer_numbers=['137','138','139'], input_layer=layers['136'], kernel_size=[3,3,3,320], name='Mixed_4f/Branch_1/Conv3d_0b_3x3', is_training=False))

            layers.update(self._unit_3d(layer_numbers=['140','141','142'], input_layer=layers['130'], kernel_size=[1,1,1,32], name='Mixed_4f/Branch_2/Conv3d_0a_1x1', is_training=False))

            layers.update(self._unit_3d(layer_numbers=['143','144','145'], input_layer=layers['142'], kernel_size=[3,3,3,128], name='Mixed_4f/Branch_2/Conv3d_0b_3x3', is_training=False))

            layers['146'] = max_pool3d_layer(layers['130'], filter_dims=[1,3,3,3,1], stride_dims=[1,1,1,1,1], padding='SAME', name='RGB/inception_i3d/Mixed_4f/Branch_3/MaxPool3d_0a_3x3')

            layers.update(self._unit_3d(layer_numbers=['147','148','149'], input_layer=layers['146'], kernel_size=[1,1,1,128], name='Mixed_4f/Branch_3/Conv3d_0b_1x1', is_training=False))

            layers['150'] = tf.concat([layers['133'], layers['139'], layers['145'], layers['149']], 4)

            #### END OF MIXED_4f ####

            layers['151'] = max_pool3d_layer(layers['150'], filter_dims=[1,2,2,2,1], stride_dims=[1,2,2,2,1], padding='SAME', name='RGB/inception_i3d/MaxPool3d_5a_2x2')

            #### Mixed_5b ####

            layers.update(self._unit_3d(layer_numbers=['152','153','154'], input_layer=layers['151'], kernel_size=[1,1,1,256], name='Mixed_5b/Branch_0/Conv3d_0a_1x1', is_training=False))

            layers.update(self._unit_3d(layer_numbers=['155','156','157'], input_layer=layers['151'], kernel_size=[1,1,1,160], name='Mixed_5b/Branch_1/Conv3d_0a_1x1', is_training=False))

            layers.update(self._unit_3d(layer_numbers=['158','159','160'], input_layer=layers['157'], kernel_size=[3,3,3,320], name='Mixed_5b/Branch_1/Conv3d_0b_3x3', is_training=False))

            layers.update(self._unit_3d(layer_numbers=['161','162','163'], input_layer=layers['151'], kernel_size=[1,1,1,32], name='Mixed_5b/Branch_2/Conv3d_0a_1x1', is_training=False))

            layers.update(self._unit_3d(layer_numbers=['164','165','166'], input_layer=layers['163'], kernel_size=[3,3,3,128], name='Mixed_5b/Branch_2/Conv3d_0a_3x3', is_training=False))

            layers['167'] = max_pool3d_layer(layers['151'], filter_dims=[1,3,3,3,1], stride_dims=[1,1,1,1,1], padding='SAME', name='RGB/inception_i3d/Mixed_5b/Branch_3/MaxPool3d_0a_3x3')

            layers.update(self._unit_3d(layer_numbers=['168','169','170'], input_layer=layers['167'], kernel_size=[1,1,1,128], name='Mixed_5b/Branch_3/Conv3d_0b_1x1', is_training=False))

            layers['171'] = tf.concat([layers['154'], layers['160'], layers['166'], layers['170']], 4)

            #### END OF MIXED_5b ####

            #### Mixed_5c ####

            layers.update(self._unit_3d(layer_numbers=['172','173','174'], input_layer=layers['171'], kernel_size=[1,1,1,384], name='Mixed_5c/Branch_0/Conv3d_0a_1x1', is_training=False))

            layers.update(self._unit_3d(layer_numbers=['175','176','177'], input_layer=layers['171'], kernel_size=[1,1,1,192], name='Mixed_5c/Branch_1/Conv3d_0a_1x1', is_training=False))

            layers.update(self._unit_3d(layer_numbers=['178','179','180'], input_layer=layers['177'], kernel_size=[3,3,3,384], name='Mixed_5c/Branch_1/Conv3d_0b_3x3', is_training=False))

            layers.update(self._unit_3d(layer_numbers=['181','182','183'], input_layer=layers['171'], kernel_size=[1,1,1,48], name='Mixed_5c/Branch_2/Conv3d_0a_1x1', is_training=False))

            layers.update(self._unit_3d(layer_numbers=['184','185','186'], input_layer=layers['183'], kernel_size=[3,3,3,128], name='Mixed_5c/Branch_2/Conv3d_0b_3x3', is_training=False))

            layers['187'] = max_pool3d_layer(layers['171'], filter_dims=[1,3,3,3,1], stride_dims=[1,1,1,1,1], padding='SAME', name='RGB/inception_i3d/Mixed_5c/Branch_3/MaxPool3d_0a_3x3')

            layers.update(self._unit_3d(layer_numbers=['188','189','190'], input_layer=layers['187'], kernel_size=[1,1,1,128], name='Mixed_5c/Branch_3/Conv3d_0b_1x1', is_training=False))

            layers['191'] = tf.concat([layers['174'], layers['180'], layers['186'], layers['190']], 4)

            #### END OF MIXED_5c ####

            layers['192'] = tf.expand_dims(tf.reduce_mean(avg_pool3d_layer(layers['191'], filter_dims=[1,2,7,7,1], stride_dims=[1,1,1,1,1], padding='VALID', name='RGB/inception_i3d/avg_pooling'), axis=1), 1)

            layers['193'] = dropout(layers['192'], rate=dropout_rate, training=is_training)

            layers.update(self._unit_3d(layer_numbers=['logits_pre'], input_layer=layers['193'], kernel_size=[1,1,1,output_dims], name='RGB/inception_i3d/Logits/Conv3d_0c_1x1', is_training=is_training, activation_fn=None, use_batch_norm=False, freeze=True))

            layers['logits'] = tf.expand_dims(tf.reduce_mean(tf.squeeze(layers['logits_pre'], [2, 3]), axis=1), 1)

        # END WITH

        return [layers[x] for x in return_layer]

    def load_default_weights(self):
        """
        return: Numpy dictionary containing the names and values of the weight tensors used to initialize this model
        """
        return np.load('models/weights/i3d_rgb_kinetics.npy')

    def preprocess_tfrecords(self, input_data_tensor, frames, height, width, channel, input_dims, output_dims, seq_length, size, label, istraining, video_step):
        """
        Args:
            :input_data_tensor:     Data loaded from tfrecords containing either video or clips
            :frames:                Number of frames in loaded video or clip
            :height:                Pixel height of loaded video or clip
            :width:                 Pixel width of loaded video or clip
            :channel:               Number of channels in video or clip, usually 3 (RGB)
            :input_dims:            Number of frames used in input
            :output_dims:           Integer number of classes in current dataset
            :seq_length:            Length of output sequence
            :size:                  List detailing values of height and width for final frames
            :label:                 Label for loaded data
            :is_training:           Boolean value indication phase (TRAIN OR TEST)
            :video_step:            Tensorflow variable indicating the total number of videos (not clips) that have been loaded
        """
        return preprocess(input_data_tensor, frames, height, width, channel, input_dims, output_dims, seq_length, size, label, istraining, self.input_alpha)


    """ Function to return loss calculated on given network """
    def loss(self, logits, labels, loss_type='full_loss'):
        """
        Args:
            :logits: Unscaled logits returned from final layer in model
            :labels: True labels corresponding to loaded data

        Return:
            Cross entropy loss value
        """
        labels = tf.cast(labels, tf.int64)

        cross_entropy_loss = tf.losses.sparse_softmax_cross_entropy(labels=labels,
                                                                  logits=logits)
        return cross_entropy_loss




""" Base testing setup to check if model loads
if __name__=="__main__":

    x = tf.placeholder(tf.float32, shape=(6, 64, 224, 224, 3))
    y = tf.placeholder(tf.int32, [10])
    network = I3D(16)
    XX =  network.inference(x, is_training=True, input_dims=16, output_dims=51, seq_length=50, scope='RGB/inception_i3d')
    sess = tf.Session()
    init = tf.global_variables_initializer()
    sess.run(init)
    import pdb; pdb.set_trace()
"""
