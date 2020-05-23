import tensorflow as tf
import numpy as np
import os, pdb
import cv2
import numpy as np
import random as rn
import threading
import time
from sklearn import metrics
import utils
global n_classes
import triplet_loss as tri
import os.path


'''
#===========================================================================
Parameters:
        TRAIN_WO_SPEC_GAN: Excluding GAN. E.f. progressGAN means We dont include progressGAN for tranining phase
        n_classes: Number of classes (2 for now. one for fake and one for real)
        data_dir:  The path to the file list directory
        image_dir: The path to the images directory (if the image list is stored in absoluate path, set this to './')
        margin:    Marginal value in triplet loss function
Data Preparation
        All training image list should put on the subfolder 'data' named by train_wo_[TRAIN_WO_SPEC_GAN].txt, wheere
        the text file should have image path with its label (which GAN) such that 
        image_path1 0
        image_path2 1
        image_path3 5
        image_path4 0
        The data list in validation set is the same structure with training set.
#===========================================================================
'''
TRAIN_WO_SPEC_GAN = 'all'                         
n_classes = 2
#data_dir = '../../DCGAN-LSGAN-WGAN-WGAN-GP-Tensorflow/data/'
#image_dir = '../../DCGAN-LSGAN-WGAN-WGAN-GP-Tensorflow/'
batch_size = 64
display_step = 80
learning_rate = tf.placeholder(tf.float32)      # Learning rate to be fed
lr = 1e-4     
margin = 0.8

#========================Mode basic components============================
def activation(x,name="activation"):
    return tf.nn.swish(x)
    
def conv2d(name, l_input, w, b, s, p):
    l_input = tf.nn.conv2d(l_input, w, strides=[1,s,s,1], padding=p, name=name)
    l_input = l_input+b

    return l_input

def batchnorm(conv, isTraining, name='bn'):
    return tf.layers.batch_normalization(conv, training=isTraining, name="bn"+name)

def initializer(in_filters, out_filters, name, k_size=3):
    w1 = tf.get_variable(name+"W", [k_size, k_size, in_filters, out_filters], initializer=tf.truncated_normal_initializer())
    b1 = tf.get_variable(name+"B", [out_filters], initializer=tf.truncated_normal_initializer())
    return w1, b1
  
def residual_block(in_x, in_filters, out_filters, stride, isDownSampled, name, isTraining, k_size=3):
    global ema_gp
    # first convolution layer
    if isDownSampled:
      in_x = tf.nn.avg_pool(in_x, ksize=[1, 2, 2, 1], strides=[1, 2, 2, 1], padding='VALID')
      
    x = batchnorm(in_x, isTraining, name=name+'FirstBn')
    x = activation(x)
    w1, b1 = initializer(in_filters, in_filters, name+"first_res", k_size=k_size)
    x = conv2d(name+'r1', x, w1, b1, 1, "SAME")

    # second convolution layer
    x = batchnorm(x, isTraining, name=name+'SecondBn')
    x = activation(x)
    w2, b2 = initializer(in_filters, out_filters, name+"Second_res",k_size=k_size)
    x = conv2d(name+'r2', x, w2, b2, 1, "SAME")
    
    if in_filters != out_filters:
        difference = out_filters - in_filters
        left_pad = difference // 2
        right_pad = difference - left_pad
        identity = tf.pad(in_x, [[0, 0], [0, 0], [0, 0], [left_pad, right_pad]])
        return x + identity
    else:
        return in_x + x


'''
#===========================================================================
Network architecture based on ResNet
#===========================================================================
'''      
def ResNet(_X, isTraining):
    global n_classes
    w1 = tf.get_variable("initWeight", [7, 7, 3, 64], initializer=tf.truncated_normal_initializer())
    b1 = tf.get_variable("initBias", [64], initializer=tf.truncated_normal_initializer())
    initx = conv2d('conv1', _X, w1, b1, 4, "VALID")
    
    filters_num = [64,96,128]
    block_num = [2,4,3]
    l_cnt = 1
    x = initx
    
    # ============Feature extraction network with kernel size 3x3============
    
    for i in range(len(filters_num)):
        for j in range(block_num[i]):
          
            if ((j==block_num[i]-1) & (i<len(filters_num)-1)):
                x = residual_block(x, filters_num[i], filters_num[i+1], 2, True, 'ResidualBlock%d'%(l_cnt), isTraining)
                print('[L-%d] Build %dth connection layer %d from %d to %d channels' % (l_cnt, i, j, filters_num[i], filters_num[i+1]))
            else:
                x = residual_block(x, filters_num[i], filters_num[i], 1, False, 'ResidualBlock%d'%(l_cnt), isTraining)
                print('[L-%d] Build %dth residual block %d with %d channels' % (l_cnt,i, j, filters_num[i]))
            l_cnt +=1
    
    layer_33 = x
    x = initx
    
    # ============Feature extraction network with kernel size 5x5============
    for i in range(len(filters_num)):
        for j in range(block_num[i]):
          
            if ((j==block_num[i]-1) & (i<len(filters_num)-1)):
                x = residual_block(x, filters_num[i], filters_num[i+1], 2, True, 'Residual5Block%d'%(l_cnt), isTraining, k_size=5)
                print('[L-%d] Build %dth connection layer %d from %d to %d channels' % (l_cnt, i, j, filters_num[i], filters_num[i+1]))
            else:
                x = residual_block(x, filters_num[i], filters_num[i], 1, False, 'Residual5Block%d'%(l_cnt), isTraining, k_size=5)
                print('[L-%d] Build %dth residual block %d with %d channels' % (l_cnt,i, j, filters_num[i]))
            l_cnt +=1
    layer_55 = x
    print("Layer33's shape", layer_33.get_shape().as_list())
    print("Layer55's shape", layer_55.get_shape().as_list())

    x = tf.concat([layer_33, layer_55], 3)
    
    # ============ Classifier Learning============
    
    x_shape = x.get_shape().as_list()
    dense1 = x_shape[1]*x_shape[2]*x_shape[3]
    W = tf.get_variable("featW", [dense1, 128], initializer=tf.truncated_normal_initializer())
    b = tf.get_variable("featB", [128], initializer=tf.truncated_normal_initializer())
    dense1 = tf.reshape(x, [-1, dense1])
    feat = tf.nn.softmax(tf.matmul(dense1, W) + b)
    
    with tf.variable_scope('Final'):
        x = batchnorm(x, isTraining, name='FinalBn')
        x = activation(x)
        wo, bo=initializer(filters_num[-1]*2, n_classes, "FinalOutput")
        x = conv2d('final', x, wo, bo, 1, "SAME")
        saliency = tf.argmax(x, 3)
        x=tf.reduce_mean(x, [1, 2])

        W = tf.get_variable("FinalW", [n_classes, n_classes], initializer=tf.truncated_normal_initializer())
        b = tf.get_variable("FinalB", [n_classes], initializer=tf.truncated_normal_initializer())

        out = tf.matmul(x, W) + b
                            

    return out, feat, saliency


#==========================================================================
#=============Reading data in multithreading manner========================
#==========================================================================
def read_labeled_image_list(image_list_file, training_img_dir):
    f = open(image_list_file, 'r')
    filenames = []
    labels = []

    for line in f:
        filename, label = line[:-1].split(' ')
        filename = training_img_dir+filename
        filenames.append(filename)
        labels.append(int(label))
        
    return filenames, labels
    
    
def read_images_from_disk(input_queue, size1=64):
    label = input_queue[1]
    fn=input_queue[0]
    file_contents = tf.read_file(input_queue[0])
    example = tf.image.decode_jpeg(file_contents, channels=3)
    
    #example = tf.image.decode_png(file_contents, channels=3, name="dataset_image") # png fo rlfw
    example=tf.image.resize_images(example, [size1,size1])
    return example, label, fn
    
def setup_inputs(sess, filenames, training_img_dir, image_size=64, crop_size=64, isTest=False, batch_size=128):
    
    # Read each image file
    image_list, label_list = read_labeled_image_list(filenames, training_img_dir)

    images = tf.cast(image_list, tf.string)
    labels = tf.cast(label_list, tf.int64)
     # Makes an input queue
    if isTest is False:
        isShuffle = True
        numThr = 4
    else:
        isShuffle = False
        numThr = 1
        
    input_queue = tf.train.slice_input_producer([images, labels], shuffle=isShuffle)
    image, y,fn = read_images_from_disk(input_queue)

    channels = 3
    image.set_shape([None, None, channels])
        
    # Crop and other random augmentations
    if isTest is False:
        image = tf.image.random_flip_left_right(image)
        image = tf.image.random_saturation(image, .95, 1.05)
        image = tf.image.random_brightness(image, .05)
        image = tf.image.random_contrast(image, .95, 1.05)
        
    image = tf.cast(image, tf.float32)/255.0
    
    image, y,fn = tf.train.batch([image, y, fn], batch_size=batch_size, capacity=batch_size*3, num_threads=numThr, name='labels_and_images')

    tf.train.start_queue_runners(sess=sess)

    return image, y, fn, len(label_list)

'''
Maia training function:


'''
if not os.path.isdir('logs'):
    os.mkdir('logs')
if not os.path.isdir('saliency_img'):
    os.mkdir('saliency_img')
if not os.path.isdir('logs/pair'):
    os.mkdir('logs/pair')
if not os.path.isdir('logs/pair/%s/'%(TRAIN_WO_SPEC_GAN)):
    os.mkdir('logs/pair/%s/'%(TRAIN_WO_SPEC_GAN))

                   # Learning rate start
tst = tf.placeholder(tf.bool)
iter = tf.placeholder(tf.int32)
print('GO!!')


# In[ ]:


# Setup the tensorflow...
config = tf.ConfigProto()
config.gpu_options.allow_growth = True
sess = tf.Session(config=config)

train_file = "/home/ubuntu/prep_data/cffn_classifier_train.txt"
val_file = "/home/ubuntu/prep_data/cffn_classifier_val.txt"
#pairs_file = "/home/ubuntu/prep_data/cffn_pairs.txt"
image_dir = "/home/ubuntu/prep_data/"

print("Preparing the training & validation data...")
#pth1 = os.path.join(data_dir, "train_wo_%s.txt"%(TRAIN_WO_SPEC_GAN))
train_data, train_labels, filelist1, glen1 = setup_inputs(sess, train_file, image_dir, batch_size=batch_size)
#pth2 = os.path.join(data_dir, "val_wo_%s.txt"%(TRAIN_WO_SPEC_GAN))
val_data, val_labels, filelist2, tlen1 = setup_inputs(sess, val_file, image_dir, batch_size=10,isTest=True)
print("Found %d training images, and %d validation images..." % (glen1, tlen1))

max_iter = glen1*80
print("Preparing the training model with learning rate = %.5f..." % (lr))

# Initialize the model for training set and validation sets
with tf.variable_scope("ResNet") as scope:
    pred, feat,_ = ResNet(train_data, True)
    scope.reuse_variables()
    valpred, _, saliency = ResNet(val_data, False)


# Forming the triplet loss by hard-triplet sampler  
with tf.name_scope('Triplet_loss'):
    
    sialoss = tri.batch_hard_triplet_loss(train_labels, feat, margin, squared=False)

# Forming the cross-entropy loss and accuracy for classifier learning
with tf.name_scope('Loss_and_Accuracy'):
    update_ops = tf.get_collection(tf.GraphKeys.UPDATE_OPS)
    with tf.control_dependencies(update_ops):
        t_vars=tf.trainable_variables() 
        #t_vars=[var for var in t_vars if 'Final']
        cost = tf.losses.sparse_softmax_cross_entropy(labels=train_labels, logits=pred)
        optimizer = tf.train.AdamOptimizer(learning_rate=learning_rate).minimize(cost, var_list=t_vars)
        sia_optimizer = tf.train.AdamOptimizer(learning_rate=learning_rate).minimize(sialoss)

    correct_prediction = tf.equal(tf.argmax(pred, 1), train_labels)
    accuracy = tf.reduce_mean(tf.cast(correct_prediction, tf.float32))
    correct_prediction2 = tf.equal(tf.argmax(valpred, 1), val_labels)
    accuracy2 = tf.reduce_mean(tf.cast(correct_prediction2, tf.float32))

  
tf.summary.scalar("Triplet_loss", sialoss)
tf.summary.scalar('Loss', cost)
tf.summary.scalar('Training_Accuracy', accuracy)
tf.summary.scalar('Validation_Accuracy', accuracy2)
# In[ ]:


saver = tf.train.Saver()
init = tf.global_variables_initializer()
sess.run(init)
step = 0

writer = tf.summary.FileWriter("logs/pair/%s/"%(TRAIN_WO_SPEC_GAN), sess.graph)
summaries = tf.summary.merge_all()

print("We are going to train fake detector using ResNet based on triplet loss!!!")
print("glen1 " + str(glen1))
start_lr = lr
while (step * batch_size) < max_iter:
    epoch1=np.floor((step*batch_size)/glen1)
    if (((step*batch_size)%glen1 < batch_size) & (lr==1e-4) & (epoch1 >=3)):
        lr /= 10
    
    if epoch1 <=3:
        sess.run([sia_optimizer],  feed_dict={learning_rate: lr})
    else:
        sess.run([optimizer],  feed_dict={learning_rate: lr})
        
    if (step % 15000==1) & (step>15000):
        save_path = saver.save(sess, "checkpoints/tf_deepUD_tri_model_iter_%d_for_%s.ckpt" % (step,TRAIN_WO_SPEC_GAN))
        print("Model saved in file at iteration %d: %s" % (step*batch_size,save_path))

    if step>0 and step % display_step == 0:
        # calculate the loss
        loss, acc, summaries_string, sia_val = sess.run([cost, accuracy, summaries, sialoss])
        print("Iter=%d/epoch=%d, Loss=%.6f, Triplet loss=%.6f, Training Accuracy=%.6f, lr=%f" % (step*batch_size, epoch1 ,loss, sia_val, acc, lr))
        writer.add_summary(summaries_string, step)
    
    if step>0 and (step % (display_step*20) == 0):
        rounds = tlen1 // 1000
        #pdb.set_trace()
        valacc=[]
        vis=[]
        tis=[]
        for k in range(rounds):
            a2, vi, ti = sess.run([accuracy2, tf.argmax(valpred, 1), val_labels])
            valacc.append(a2)
            vis.append(vi)
            tis.append(ti)
        tis = np.reshape(np.asarray(tis), [-1])
        vis = np.reshape(np.asarray(vis), [-1])
        precision=metrics.precision_score(tis, vis) 
        recall=metrics.recall_score(tis, vis)
        
        sal, valimg = sess.run([saliency, val_data])
        utils.batchsalwrite(valimg, sal, tis, vis, 'saliency_img/%s_Detected_'%(TRAIN_WO_SPEC_GAN))
        

        print("Iter=%d/epoch=%d, Validation Accuracy=%.6f, Precision=%.6f, Recall=%.6f" % (step*batch_size, epoch1 , np.mean(valacc), precision, recall))

  
    step += 1
print("Optimization Finished!")
save_path = saver.save(sess, "checkpoints/tf_deepUD_tri_model_%s.ckpt" % (TRAIN_WO_SPEC_GAN))
print("Model saved in file: %s" % save_path)