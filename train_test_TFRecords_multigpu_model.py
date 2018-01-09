# Basic imports
import os
import time
import argparse
import tensorflow      as tf
import numpy           as np
import multiprocessing as mp

# Tensorflow ops imports
from tensorflow.python.ops      import clip_ops
from tensorflow.python.ops      import init_ops
from tensorflow.python.ops      import control_flow_ops
from tensorflow.python.ops      import variable_scope as vs
from tensorflow.python.ops      import variables as vars_
from tensorflow.python.training import queue_runner_impl

# Custom imports
from models                 import *
from utils                  import initialize_from_dict, save_checkpoint, load_checkpoint, make_dir
from Queue                  import Queue
from logger                 import Logger
from random                 import shuffle
from load_dataset_tfrecords import load_dataset

def _average_gradients(tower_grads):
    """
    Calculate the average gradient for each shared variable across all towers.
    Note that this function provides a synchronization point across all towers.
    Args:
        tower_grads: List of lists of (gradient, variable) tuples. The outer list
                     is over individual gradients. The inner list is over the gradient
                     calculation for each tower.
    Returns:
        List of pairs of (gradient, variable) where the gradient has been averaged
        across all towers.
    """

    average_grads = []

    for grad_and_vars in zip(*tower_grads):
        # Note that each grad_and_vars looks like the following:
        #   ((grad0_gpu0, var0_gpu0), ... , (grad0_gpuN, var0_gpuN))
        grads = []

        for g, _ in grad_and_vars:
            # Add 0 dimension to the gradients to represent the tower.
            expanded_g = tf.expand_dims(g, 0)

            # Append on a 'tower' dimension which we will average over below.
            grads.append(expanded_g)

        # END FOR

        # Average over the 'tower' dimension.
        grad = tf.concat(axis=0, values=grads)
        grad = tf.reduce_mean(grad, 0)

        # Keep in mind that the Variables are redundant because they are shared
        # across towers. So .. we will just return the first tower's pointer to
        # the Variable.
        v = grad_and_vars[0][1]
        grad_and_var = (grad, v)
        average_grads.append(grad_and_var)

    # END FOR
    return average_grads




def train(model, input_dims, output_dims, seq_length, size, num_gpus, dataset, experiment_name, load_model, num_vids, n_epochs, split, base_data_path, f_name, learning_rate_init, wd, save_freq, val_freq, return_layer, k=25):

    """
    Training function used to train or fine-tune a chosen model
    Args:
        :model:              tf-activity-recognition framework model object
        :input_dims:         Number of frames used in input
        :output_dims:        Integer number of classes in current dataset
        :seq_length:         Length of output sequence expected from LSTM
        :size:               List detailing height and width of frame
        :num_gpus:           Number of gpus to use when training
        :dataset:            Name of dataset being processed
        :experiment_name:    Name of current experiment
        :load_model:         Boolean variable indicating whether to load form a checkpoint or not
        :num_vids:           Number of videos to be used for training
        :n_epochs:           Total number of epochs to train
        :split:              Split of dataset being used
        :base_data_path:     Full path to root directory containing datasets
        :f_name:             Specific video directory within a chosen split of a dataset 
        :learning_rate_init: Initializer for learning rate
        :wd:                 Weight decay
        :save_freq:          Frequency, in epochs, with which to save
        :val_freq:           Frequency, in epochs, with which to run validaton
        :return_layer:       Layers to be tracked during training
        :k:                  Width of temporal sliding window

    Returns:
        Does not return anything
    """

    with tf.name_scope("my_scope") as scope:

        # Ensure first layer requested in return sequence is "logits" always
        if return_layer[0] != 'logits':
            return_layer.insert(0, 'logits')

        # END IF

        # Initializers for checkpoint and global step variable
        ckpt    = None
        gs_init = 0

        # Load pre-trained/saved model to continue training (or fine-tune)
        if load_model:
            try:
                ckpt, gs_init, learning_rate_init = load_checkpoint(model.name, dataset, experiment_name)
                print 'A better checkpoint is found. Its global_step value is: ' + str(gs_init)

            except:
                print "Failed loading checkpoint requested. Please check."
                exit()

            # END TRY
        else:
            ckpt = model.load_default_weights()

        # END IF

        # Initialize model variables
        global_step        = tf.Variable(gs_init, name='global_step', trainable=False)
        istraining         = True
        reuse_variables    = None

        model_params_array = []
        tower_losses       = []
        tower_grads        = []
        tower_slogits      = []

        # Make this a part of the model initialization itself instead of here (to make training function generic)
        j               = input_dims / k

        data_path = os.path.join(base_data_path, 'tfrecords_'+dataset, 'Split'+str(split), f_name)

        # Setup tensors for models
        input_data_tensor, labels_tensor, names_tensor = load_dataset(model, num_gpus, output_dims, input_dims, seq_length, size, data_path, dataset, istraining)

        # Define optimizer (Current selection is only momentum optimizer)
        optimizer = lambda lr: tf.train.MomentumOptimizer(learning_rate=lr, momentum=0.9)

        """ Multi-GPU setup: 1) Associate gpu device to specific model replica
                             2) Setup tower name scope for variables
        """
        for gpu_idx in range(num_gpus):
            with tf.device('/gpu:'+str(gpu_idx)):   
                with tf.name_scope('%s_%d' % ('tower', gpu_idx)) as scope:
                    with tf.variable_scope(tf.get_variable_scope(), reuse = reuse_variables):
                        returned_layers = model.inference(input_data_tensor[gpu_idx,:,:,:,:],
                                                 istraining,
                                                 input_dims,
                                                 output_dims,
                                                 seq_length,
                                                 scope, k, j,
                                                 return_layer = return_layer,
                                                 weight_decay=wd)

                        logits       = returned_layers[0]
                        model_params = returned_layers[1:]

                        model_params_array.append(model_params)

                        # Calculating Softmax for probability outcomes : Can be modified, make function internal to model
                        slogits = tf.nn.softmax(logits)

                        lr = vs.get_variable("learning_rate", [],trainable=False,initializer=init_ops.constant_initializer(learning_rate_init))

                    # END WITH

                    reuse_variables = True

                    """ Within GPU mini-batch: 1) Calculate loss,
                                               2) Initialize optimizer with required learning rate and
                                               3) Compute gradients
                                               4) Aggregate losses, gradients and logits
                    """
                    total_loss = model.loss(logits, labels_tensor[gpu_idx, :])
                    opt        = optimizer(lr)
                    gradients  = opt.compute_gradients(total_loss, vars_.trainable_variables())

                    tower_losses.append(total_loss)
                    tower_grads.append(gradients)
                    tower_slogits.append(slogits)

                # END WITH

            # END WITH

        # END FOR

        model_params_array = np.array(model_params_array).T.tolist()

        """  After: 1) Computing gradients and losses need to be stored and averaged
                    2) Clip gradients by norm to required value
                    3) Apply mean gradient updates
        """

        gradients            = _average_gradients(tower_grads)
        gradients, variables = zip(*gradients)
        clipped_gradients, _ = clip_ops.clip_by_global_norm(gradients, 5.0)
        gradients            = list(zip(clipped_gradients, variables))
        grad_updates         = opt.apply_gradients(gradients, global_step=global_step, name="train")
        train_op             = grad_updates


        # Logging setup initialization (Naming format: Date, month, hour, minute, second)
        log_name     = ("exp_train_%s_%s_%s" % ( time.strftime("%d_%m_%H_%M_%S"),
                                                           dataset,
                                                           experiment_name))
        make_dir(os.path.join('results',model.name))
        make_dir(os.path.join('results',model.name, dataset))
        make_dir(os.path.join('results',model.name, dataset, experiment_name))
        make_dir(os.path.join('results',model.name, dataset, experiment_name, 'checkpoints'))
        curr_logger = Logger(os.path.join('logs',model.name,dataset, log_name))

        # TF session setup
        config  = tf.ConfigProto(allow_soft_placement=True)
        sess    = tf.Session(config=config)
        init    = tf.global_variables_initializer()
        coord   = tf.train.Coordinator()
        threads = queue_runner_impl.start_queue_runners(sess=sess, coord=coord)

        # Variables get randomly initialized into tf graph
        sess.run(init)

        # Model variables initialized from previous saved models
        initialize_from_dict(sess, ckpt)

        acc            = 0
        epoch_count    = 0
        tot_load_time  = 0.0
        tot_train_time = 0.0

        losses     = []
        total_pred = []
        save_data  = []

        lr            = learning_rate_init
        learning_rate = lr

        # Timing test setup
        time_init = time.time()

        # Loop epoch number of time over the training set
        for tot_count in range(0, n_epochs*num_vids, num_gpus):

            # Variable to update during epoch intervals
            for gpu_idx in range(num_gpus):
                if tot_count % num_vids == gpu_idx:
                    batch_count = 0
                    epoch_acc   = 0

                    if epoch_count % save_freq == 0 and tot_count > 0:
                        print "Saving..."
                        save_checkpoint(sess, model.name, dataset, experiment_name, learning_rate, global_step.eval(session=sess))

                    # END IF

                    epoch_count += 1

                # END IF

            # END FOR

            time_pre_train = time.time()

            _, loss_train, predictions, gs, labels, params = sess.run([train_op, tower_losses,
                                                                       tower_slogits, global_step,
                                                                       labels_tensor, model_params_array])
            params = np.array(params)


            # Compute training epoch accuracy
            for pred_idx in range(len(predictions)):
                pred = np.mean(predictions[pred_idx], 0).argmax()

                if pred == labels[pred_idx][0]:
                    epoch_acc +=1

                # END IF

                batch_count+=1

            # END FOR

            time_post_train = time.time()
            tot_train_time += time_post_train - time_pre_train


            print 'train_time: ', time_post_train-time_pre_train
            print 'step, loss: ', gs, loss_train

            curr_logger.add_scalar_value('train/train_time',time_post_train - time_pre_train, step=gs)
            curr_logger.add_scalar_value('train/loss',      float(np.mean(loss_train)), step=gs)
            curr_logger.add_scalar_value('train/epoch_acc', epoch_acc/float(batch_count), step=gs)
            for p in range(params.shape[0]):
                curr_logger.add_scalar_value('train/'+str(return_layer[1:][p]), float(np.mean(params[p])), step=gs)

            # END FOR 

        # END FOR

        print "Saving..."
        save_checkpoint(sess, model.name, dataset, experiment_name, learning_rate, gs)
        coord.request_stop()
        coord.join(threads)


        print "Tot train time: ", tot_train_time
        print "Tot time:       ", time.time()-time_init

    # END WITH



def _clip_logits(model, input_data_tensor, istraining, input_dims, output_dims, seq_length, scope, k, j):
    """
    Function used to return logits and softmax(logits) from a chosen model for clip inputs
    Args:
        :model:              tf-activity-recognition framework model object
        :input_data_tensor:  Tensor containing input data
        :istraining:         Boolean variable indicating training/testing phase 
        :input_dims:         Number of frames used in input
        :output_dims:        Integer number of classes in current dataset
        :seq_length:         Length of output sequence expected from LSTM
        :scope:              String indicating current scope name
        :k:                  Width of temporal sliding window
        :j:                  Number of sets in input data once temporal window of length k is applied

    Returns:
        logits from network and Softmax(logits)
    """
    # Model Inference
    logits_list = tf.map_fn(lambda clip_tensor: model.inference(clip_tensor,
                             istraining,
                             input_dims,
                             output_dims,
                             seq_length,
                             scope, k, j)[0], input_data_tensor[0,:,:,:,:,:])

    # Logits
    softmax = tf.map_fn(lambda logits: tf.nn.softmax(logits), logits_list)

    return logits_list, softmax

def _video_logits(model, input_data_tensor, istraining, input_dims, output_dims, seq_length, scope, k, j):
    """
    Function used to return logits and softmax(logits) from a chosen model  for a single input
    Args:
        :model:              tf-activity-recognition framework model object
        :input_data_tensor:  Tensor containing input data
        :istraining:         Boolean variable indicating training/testing phase 
        :input_dims:         Number of frames used in input
        :output_dims:        Integer number of classes in current dataset
        :seq_length:         Length of output sequence expected from LSTM
        :scope:              String indicating current scope name
        :k:                  Width of temporal sliding window
        :j:                  Number of sets in input data once temporal window of length k is applied

    Returns:
        logits from network and Softmax(logits)
    """

    # Model Inference
    logits = model.inference(input_data_tensor[0,:,:,:,:],
                             istraining,
                             input_dims,
                             output_dims,
                             seq_length,
                             scope, k, j)[0]

    # Logits
    softmax = tf.nn.softmax(logits)

    return logits, softmax

def test(model, input_dims, output_dims, seq_length, size, dataset, loaded_dataset, experiment_name, num_vids, split, base_data_path, f_name, load_model, k=25):

    """
    Function used to test the performance and analyse a chosen model
    Args:
        :model:              tf-activity-recognition framework model object
        :input_dims:         Number of frames used in input
        :output_dims:        Integer number of classes in current dataset
        :seq_length:         Length of output sequence expected from LSTM
        :size:               List detailing height and width of frame
        :dataset:            Name of dataset being loaded
        :loaded_dataset:     Name of dataset which was used to train the current model
        :experiment_name:    Name of current experiment
        :num_vids:           Number of videos to be used for training
        :split:              Split of dataset being used
        :base_data_path:     Full path to root directory containing datasets
        :f_name:             Specific video directory within a chosen split of a dataset 
        :k:                  Width of temporal sliding window

    Returns:
        Does not return anything
    """

    with tf.name_scope("my_scope") as scope:

        # Initializers for checkpoint and global step variable
        ckpt    = None
        gs_init = 0

        # Load pre-trained/saved model
        if load_model:
            try:
                ckpt, gs_init, learning_rate_init = load_checkpoint(model.name, dataset, experiment_name)
                print 'A better checkpoint is found. Its global_step value is: ' + str(gs_init)

            except:
                print "Failed loading checkpoint requested. Please check."
                exit()

            # END TRY
        else:
            ckpt = model.load_default_weights()

        # END IF

        # Initialize model variables
        istraining  = False
        global_step = tf.Variable(gs_init, name='global_step', trainable=False)

        j           = input_dims / k

        data_path   = os.path.join(base_data_path, 'tfrecords_'+dataset, 'Split'+str(split), f_name)

        # Setting up tensors for models
        input_data_tensor, labels_tensor, names_tensor = load_dataset(model, 1, output_dims, input_dims, seq_length, size, data_path, dataset, istraining)

        # If number of return values in data tensor > 5 implies multiple clips of a video are being returned (since our cluster has a max capacity of 4)
        if len(input_data_tensor.shape) > 5:
            logits, softmax = _clip_logits(model, input_data_tensor, istraining, input_dims, output_dims, seq_length, scope, k, j)

        else:
            logits, softmax = _video_logits(model, input_data_tensor, istraining, input_dims, output_dims, seq_length, scope, k, j)

        # END IF

        # Logger setup (Name format: Date, month, hour, minute and second, with a prefix of exp_test)
        log_name    = ("exp_test_%s_%s_%s" % ( time.strftime("%d_%m_%H_%M_%S"),
                                               dataset, experiment_name))
        curr_logger = Logger(os.path.join('logs',model.name,dataset, log_name))

        # TF session setup
        sess    = tf.Session()
        init    = (tf.global_variables_initializer(), tf.local_variables_initializer())
        coord   = tf.train.Coordinator()
        threads = queue_runner_impl.start_queue_runners(sess=sess, coord=coord)

        # Variables get randomly initialized into tf graph
        sess.run(init)

        # Model variables initialized from previous saved models
        initialize_from_dict(sess, ckpt)

        acc        = 0
        count      = 0
        total_pred = []

        print "Begin Testing"

        for vid_num in range(num_vids):
            count +=1
            output_predictions, labels, names = sess.run([softmax, labels_tensor, names_tensor])

            label = labels[0][0]


            if len(output_predictions.shape)!=2:
                output_predictions = np.mean(output_predictions, 1)

            # END IF

            guess = np.mean(output_predictions, 0).argmax()

            print "vidNum: ", vid_num
            print "vidName: ",names
            print "label:  ", label
            print "prediction: ", guess

            total_pred.append((guess, label))

            if int(guess) == int(label):
                acc += 1

            # END IF

            curr_logger.add_scalar_value('test/acc',acc/float(count), step=count)

        # END FOR

    # END WITH

    coord.request_stop()
    coord.join(threads)

    print "Total accuracy : ", acc/float(count)
    print total_pred

    #np.save(os.path.join('results', model.name, loaded_dataset, experiment_name,'test_predictions_'+dataset+'.npy'), np.array(total_pred))

if __name__=="__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument('--model', action= 'store', required=True,
            help= 'Model architecture (c3d, lrcn, tsn, vgg16, resnet)')

    parser.add_argument('--dataset', action= 'store', required=True,
            help= 'Dataset (UCF101, HMDB51)')

    parser.add_argument('--numGpus', action= 'store', type=int, default=1,
            help = 'Number of Gpus used for calculation')

    parser.add_argument('--train', action= 'store', required=True, type=int,
            help = 'Binary value to indicate training or evaluation instance')

    parser.add_argument('--load', action='store', type=int, default=0,
            help = 'Whether you want to load a saved model to train from scratch.')

    parser.add_argument('--size', action='store', required=True, type=int,
            help = 'Input frame size')

    parser.add_argument('--inputDims', action='store', required=True, type=int,
            help = 'Input Dimensions (Number of frames to pass as input to the model)')

    parser.add_argument('--outputDims', action='store', required=True, type=int,
            help = 'Output Dimensions (Number of classes in dataset)')

    parser.add_argument('--seqLength', action='store', required=True, type=int,
            help = 'Length of sequences for LSTM')

    parser.add_argument('--expName', action='store', required=True,
            help = 'Unique name of experiment being run')

    parser.add_argument('--numVids', action='store', required=True, type=int,
            help = 'Number of videos to be used for training')

    parser.add_argument('--lr', action='store', type=float, default=0.001,
            help = 'Learning Rate')

    parser.add_argument('--wd', action='store', type=float, default=0.0,
            help = 'Weight Decay')

    parser.add_argument('--nEpochs', action='store', type=int, default=1,
            help = 'Number of Epochs')

    parser.add_argument('--split', action='store', type=int, default=1,
            help = 'Dataset split to use')

    parser.add_argument('--baseDataPath', action='store', default='/z/dat',
            help = 'Path to datasets')

    parser.add_argument('--fName', action='store',
            help = 'Which dataset list to use (trainlist, testlist, vallist)')

    parser.add_argument('--saveFreq', action='store', type=int, default=1,
            help = 'Frequency in epochs to save model checkpoints')

    parser.add_argument('--valFreq', action='store', type=int, default=3,
            help = 'Frequency in epochs to validate')

    parser.add_argument('--loadedDataset', action= 'store', default='HMDB51',
            help= 'Dataset (UCF101, HMDB51)')

    parser.add_argument('--returnLayer', nargs='+',type=str, default=['logits'])

    args = parser.parse_args()

    print "Setup of current experiments: ",args
    model_name = args.model

    # Associating models
    if model_name == 'vgg16':
        model = VGG16()

    elif model_name == 'resnet':
        model = ResNet()

    elif model_name == 'resnet_RIL_interp_median_v23_2_1':
        model = ResNet_RIL_Interp_Median_v23_2_1()

    elif model_name == 'resnet_RIL_interp_median_v23_4':
        model = ResNet_RIL_Interp_Median_v23_4()

    elif model_name == 'resnet_RIL_interp_median_v23_7_1':
        model = ResNet_RIL_Interp_Median_v23_7_1()

    elif model_name == 'resnet_RIL_interp_median_v31_3':
        model = ResNet_RIL_Interp_Median_v31_3()

    elif model_name == 'resnet_RIL_interp_median_v34_3_lstm':
        model = ResNet_RIL_Interp_Median_v34_3_lstm()

    elif model_name == 'resnet_RIL_interp_median_v35_lstm':
        model = ResNet_RIL_Interp_Median_v35_lstm()

    elif model_name == 'resnet_RIL_interp_median_v36_lstm':
        model = ResNet_RIL_Interp_Median_v36_lstm()

    elif model_name == 'resnet_RIL_interp_median_v37_lstm':
        model = ResNet_RIL_Interp_Median_v37_lstm()

    elif model_name == 'resnet_RIL_interp_median_v38':
        model = ResNet_RIL_Interp_Median_v38()

    elif model_name == 'resnet_RIL_interp_median_v39':
        model = ResNet_RIL_Interp_Median_v39()

    elif model_name == 'resnet_RIL_interp_median_v40':
        model = ResNet_RIL_Interp_Median_v40()

    else:
        print("Model not found, check the import and elif statements")

    # END IF

    if args.train:
        train(  model               = model,
                input_dims          = args.inputDims,
                output_dims         = args.outputDims,
                seq_length          = args.seqLength,
                size                = [args.size, args.size],
                num_gpus            = args.numGpus,
                dataset             = args.dataset,
                experiment_name     = args.expName,
                load_model          = args.load,
                num_vids            = args.numVids,
                n_epochs            = args.nEpochs,
                split               = args.split,
                base_data_path      = args.baseDataPath,
                f_name              = args.fName,
                learning_rate_init  = args.lr,
                wd                  = args.wd,
                save_freq           = args.saveFreq,
                val_freq            = args.valFreq,
                return_layer        = args.returnLayer)

    else:
        test(   model             = model,
                input_dims        = args.inputDims,
                output_dims       = args.outputDims,
                seq_length        = args.seqLength,
                size              = [args.size, args.size],
                dataset           = args.dataset,
                loaded_dataset    = args.loadedDataset,
                experiment_name   = args.expName,
                num_vids          = args.numVids,
                split             = args.split,
                base_data_path    = args.baseDataPath,
                f_name            = args.fName,
                load_model        = args.load)

# Offer a non verbose option to remove all print statements
