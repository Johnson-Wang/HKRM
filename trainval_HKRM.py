# --------------------------------------------------------
# Pytorch multi-GPU HKRM
# Written by Chenhan Jiang, Hang Xu, based on code from Jianwei Yang
# --------------------------------------------------------
import _init_paths
import os
import sys
import numpy as np
import argparse
import pprint
import pdb
import time

import torch
import torch.nn as nn
import torch.optim as optim

from tensorboardX import SummaryWriter
import torchvision.transforms as transforms
from torch.utils.data.sampler import Sampler

from roi_data_layer.roidb import combined_roidb
from roi_data_layer.roibatchLoader import roibatchLoader
from model.utils.config import cfg, cfg_from_file, cfg_from_list, get_output_dir
from model.utils.net_utils import weights_normal_init, save_net, load_net, \
      adjust_learning_rate, save_checkpoint, clip_gradient
from model.HKRM.resnet_HKRM import resnet
import pickle



def parse_args():
    """
    Parse input arguments
    """
    parser = argparse.ArgumentParser(description='Train HKRM network')
    ## Define Model and training data
    parser.add_argument('--dataset', dest='dataset',
                        help='training dataset:ade,vg,vgbig,coco,pascal_07_12',
                        default='vg', type=str)
    parser.add_argument('--net', dest='net',
                        help='Attribute,Relation,Spatial,HKRM',
                        default='HKRM', type=str)
    parser.add_argument('--attr_size', dest='attr_size',
                        help='Attribute module output size',
                        default=256, type=int)
    parser.add_argument('--rela_size', dest='rela_size',
                        help='Relation module output size',
                        default=256, type=int)
    parser.add_argument('--spat_size', dest='spat_size',
                        help='Spatial module output size',
                        default=256, type=int)
    ## Define display and save dir
    parser.add_argument('--start_epoch', dest='start_epoch',
                        help='starting epoch',
                        default=1, type=int)
    parser.add_argument('--epochs', dest='max_epochs',
                        help='number of epochs to train',
                        default=20, type=int)
    parser.add_argument('--disp_interval', dest='disp_interval',
                        help='number of iterations to display',
                        default=100, type=int)
    parser.add_argument('--checkpoint_interval', dest='checkpoint_interval',
                        help='number of iterations to display',
                        default=10000, type=int)
    parser.add_argument('--save_dir', dest='save_dir',
                        help='directory to save models', default="exps/HKRM/models",
                        type=str)
    ## Define training parameters
    parser.add_argument('--nw', dest='num_workers',
                        help='number of worker to load data',
                        default=0, type=int)
    parser.add_argument('--cuda', dest='cuda', default=True, type=bool,
                        help='whether use CUDA')
    parser.add_argument('--mGPUs', dest='mGPUs',
                        help='whether use multiple GPUs',
                        action='store_true')
    parser.add_argument('--bs', dest='batch_size',
                        help='batch_size',
                        default=2, type=int)
    parser.add_argument('--cag', dest='class_agnostic',default=True, type=bool,
                        help='whether perform class_agnostic bbox regression')

# config optimization
    parser.add_argument('--o', dest='optimizer',
                        help='training optimizer',
                        default="sgd", type=str)
    parser.add_argument('--lr', dest='lr',
                        help='starting learning rate',
                        default=0.005, type=float)
    parser.add_argument('--lr_decay_step', dest='lr_decay_step',
                        help='step to do learning rate decay, unit is epoch',
                        default=4, type=int)
    parser.add_argument('--lr_decay_gamma', dest='lr_decay_gamma',
                        help='learning rate decay ratio',
                        default=0.1, type=float)

# set training session
    parser.add_argument('--s', dest='session',
                        help='training session',
                        default=2, type=int)

# resume trained model
    parser.add_argument('--r', dest='resume',
                        help='resume checkpoint or not',
                        default=True, type=bool)
    parser.add_argument('--checksession', dest='checksession',
                        help='checksession to load model',
                        default=1, type=int)
    parser.add_argument('--checkepoch', dest='checkepoch',
                        help='checkepoch to load model',
                        default=14, type=int)
    parser.add_argument('--checkpoint', dest='checkpoint',
                        help='checkpoint to load model',
                        default=21985, type=int)
    parser.add_argument('--ftnet', dest='ftnet',
                        help='Attribute,Relation,Spatial,baseline',
                        default='baseline', type=str)
# log and diaplay
    parser.add_argument('--use_tfboard', dest='use_tfboard',
                        help='whether use tensorflow tensorboard',
                        default=True, type=bool)
    parser.add_argument('--log_dir', dest='log_dir',
                        help='directory to save logs', default='logs',
                        type=str)

    args = parser.parse_args()
    return args


class sampler(Sampler):
  def __init__(self, train_size, batch_size):
    self.num_data = train_size
    self.num_per_batch = int(train_size / batch_size)
    self.batch_size = batch_size
    self.range = torch.arange(0,batch_size).view(1, batch_size).long()
    self.leftover_flag = False
    if train_size % batch_size:
      self.leftover = torch.arange(self.num_per_batch*batch_size, train_size).long()
      self.leftover_flag = True

  def __iter__(self):
    rand_num = torch.randperm(self.num_per_batch).view(-1,1) * self.batch_size
    self.rand_num = rand_num.expand(self.num_per_batch, self.batch_size) + self.range
    self.rand_num_view = self.rand_num.view(-1)

    if self.leftover_flag:
      self.rand_num_view = torch.cat((self.rand_num_view, self.leftover),0)

    return iter(self.rand_num_view)

  def __len__(self):
    return self.num_data

if __name__ == '__main__':
  args = parse_args()

  print('Called with args:')
  print(args)

  if args.use_tfboard:
    writer = SummaryWriter(args.log_dir)

  if args.dataset == "vg":
      args.imdb_name = "vg_train"
      args.imdbval_name = "vg_val"
      args.set_cfgs = ['ANCHOR_SCALES', '[2, 4, 8, 16, 32]', 'MAX_NUM_GT_BOXES', '50']
      cls_r_prob = pickle.load(open(cfg.DATA_DIR + '/graph/vg_graph_r.pkl', 'rb'))
      cls_r_prob = np.float32(cls_r_prob)
      cls_a_prob = pickle.load(open(cfg.DATA_DIR + '/graph/vg_graph_a.pkl', 'rb'))
      cls_a_prob = np.float32(cls_a_prob)
  elif args.dataset == "ade":
      args.imdb_name = "ade_train_5"
      args.imdbval_name = "ade_val_5"
      args.set_cfgs = ['ANCHOR_SCALES', '[2, 4, 8, 16, 32]', 'MAX_NUM_GT_BOXES', '50']
      cls_r_prob = pickle.load(open(cfg.DATA_DIR + '/graph/ade_graph_r.pkl', 'rb'))
      cls_r_prob = np.float32(cls_r_prob)
      cls_a_prob = pickle.load(open(cfg.DATA_DIR + '/graph/ade_graph_a.pkl', 'rb'))
      cls_a_prob = np.float32(cls_a_prob)
  elif args.dataset == "vgbig":
      args.imdb_name = "vg_train_big"
      args.imdbval_name = "vg_val_big"
      args.set_cfgs = ['ANCHOR_SCALES', '[2, 4, 8, 16, 32]', 'MAX_NUM_GT_BOXES', '50']
      cls_r_prob = pickle.load(open(cfg.DATA_DIR + '/graph/vg_big_graph_r.pkl', 'rb'))
      cls_r_prob = np.float32(cls_r_prob)
      cls_a_prob = pickle.load(open(cfg.DATA_DIR + '/graph/vg_big_graph_a.pkl', 'rb'))
      cls_a_prob = np.float32(cls_a_prob)
  elif args.dataset == "coco":
      args.imdb_name = "coco_2014_train+coco_2014_valminusminival"
      args.imdbval_name = "coco_2014_minival"
      args.set_cfgs = ['ANCHOR_SCALES', '[4, 8, 16, 32]', 'ANCHOR_RATIOS', '[0.5,1,2]', 'MAX_NUM_GT_BOXES', '50']
      cls_r_prob = pickle.load(open(cfg.DATA_DIR + '/graph/COCO_graph_r.pkl', 'rb'))
      cls_r_prob = np.float32(cls_r_prob)
      cls_a_prob = pickle.load(open(cfg.DATA_DIR + '/graph/COCO_graph_a.pkl', 'rb'))
      cls_a_prob = np.float32(cls_a_prob)

  args.cfg_file = "cfgs/res101_ms.yml"

  if args.cfg_file is not None:
    cfg_from_file(args.cfg_file)
  if args.set_cfgs is not None:
    cfg_from_list(args.set_cfgs)

  print('Using config:')
  pprint.pprint(cfg)
  np.random.seed(cfg.RNG_SEED)

  #torch.backends.cudnn.benchmark = True
  if torch.cuda.is_available() and not args.cuda:
    print("WARNING: You have a CUDA device, so you should probably run with --cuda")

  # train set
  # -- Note: Use validation set and disable the flipped to enable faster loading.
  cfg.TRAIN.USE_FLIPPED = True
  cfg.USE_GPU_NMS = args.cuda
  imdb, roidb, ratio_list, ratio_index = combined_roidb(args.imdb_name)
  train_size = len(roidb)

  print('{:d} roidb entries'.format(len(roidb)))
  sys.stdout.flush()

  output_dir = args.save_dir[0]
  if not os.path.exists(output_dir):
    os.makedirs(output_dir)

  sampler_batch = sampler(train_size, args.batch_size)

  dataset = roibatchLoader(roidb, ratio_list, ratio_index, args.batch_size, imdb.num_classes, training=True)

  dataloader = torch.utils.data.DataLoader(dataset, batch_size=args.batch_size,
                            sampler=sampler_batch, num_workers=args.num_workers, pin_memory=False)

  # initilize the tensor holder here.
  im_data = torch.FloatTensor(1)
  im_info = torch.FloatTensor(1)
  num_boxes = torch.LongTensor(1)
  gt_boxes = torch.FloatTensor(1)

  # ship to cuda
  if args.cuda:
    im_data = im_data.cuda()
    im_info = im_info.cuda()
    num_boxes = num_boxes.cuda()
    gt_boxes = gt_boxes.cuda()

  if args.cuda:
    cfg.CUDA = True

  # initilize the network here.
  if args.net == 'HKRM':
    module_size = [args.attr_size, args.rela_size, args.spat_size]
    fasterRCNN = resnet(imdb.classes, cls_a_prob, cls_r_prob, 101, class_agnostic=args.class_agnostic,
                            modules_size=module_size)
  elif args.net == 'Attribute':
    module_size = [args.attr_size, 0, 0]
    fasterRCNN = resnet(imdb.classes, cls_a_prob, None, 101, class_agnostic=args.class_agnostic,
                            modules_size=module_size)
  elif args.net == 'Relation':
    module_size = [0, args.rela_size, 0]
    fasterRCNN = resnet(imdb.classes, None, cls_r_prob, 101, class_agnostic=args.class_agnostic,
                            modules_size=module_size)
  elif args.net == 'Spatial':
    module_size = [0, 0, args.spat_size]
    fasterRCNN = resnet(imdb.classes, None, None, 101, class_agnostic=args.class_agnostic, modules_size=module_size)
  else:
    print('No module define')


  fasterRCNN.create_architecture()

  lr = cfg.TRAIN.LEARNING_RATE
  lr = args.lr
  #tr_momentum = cfg.TRAIN.MOMENTUM
  #tr_momentum = args.momentum

  params = []
  for key, value in dict(fasterRCNN.named_parameters()).items():
    if value.requires_grad:
      if 'bias' in key:
        params += [{'params':[value],'lr':lr*(cfg.TRAIN.DOUBLE_BIAS + 1), \
                'weight_decay': cfg.TRAIN.BIAS_DECAY and cfg.TRAIN.WEIGHT_DECAY or 0}]
      else:
        params += [{'params':[value],'lr':lr, 'weight_decay': cfg.TRAIN.WEIGHT_DECAY}]

  if args.optimizer == "adam":
    lr = lr * 0.1
    optimizer = torch.optim.Adam(params)

  elif args.optimizer == "sgd":
    optimizer = torch.optim.SGD(params, momentum=cfg.TRAIN.MOMENTUM)

  if args.resume:
    load_name = os.path.join(output_dir,
                             '{}_{}_{}_{}_{}.pth'.format(args.dataset, args.ftnet, args.checksession,
                                                                  args.checkepoch, args.checkpoint))
    print("loading checkpoint %s" % (load_name))
    checkpoint = torch.load(load_name)
    args.session = checkpoint['session']
    args.start_epoch = checkpoint['epoch']
    fasterRCNN.load_state_dict(checkpoint['model'])
    optimizer.load_state_dict(checkpoint['optimizer'])
    lr = optimizer.param_groups[0]['lr']
    if 'pooling_mode' in checkpoint.keys():
      cfg.POOLING_MODE = checkpoint['pooling_mode']
    print("loaded checkpoint %s" % (load_name))

  if args.mGPUs:
    fasterRCNN = nn.DataParallel(fasterRCNN)

  if args.cuda:
    fasterRCNN.cuda()


  iters_per_epoch = int(train_size / args.batch_size)

  for epoch in range(args.start_epoch, args.max_epochs):
    # setting to train mode
    fasterRCNN.train()
    loss_temp = 0
    start = time.time()

    if epoch % (args.lr_decay_step + 1) == 0:
        adjust_learning_rate(optimizer, args.lr_decay_gamma)
        lr *= args.lr_decay_gamma

    data_iter = iter(dataloader)
    for step in range(iters_per_epoch):
    # for step, data in enumerate(dataloader):
        data = next(data_iter)
        im_data.resize_(data[0].size()).copy_(data[0])
        im_info.resize_(data[1].size()).copy_(data[1])
        gt_boxes.resize_(data[2].size()).copy_(data[2])
        num_boxes.resize_(data[3].size()).copy_(data[3])

        fasterRCNN.zero_grad()

        rois, cls_prob, bbox_pred, \
        rpn_loss_cls, rpn_loss_box, \
        RCNN_loss_cls, RCNN_loss_bbox, \
        rois_label, adja_loss, adjr_loss = fasterRCNN(im_data, im_info, gt_boxes, num_boxes)

        loss = rpn_loss_cls.mean() + rpn_loss_box.mean()\
               + RCNN_loss_cls.mean() + RCNN_loss_bbox.mean()\
               + adja_loss.mean() + adjr_loss.mean()

        loss_temp += loss.item()

        # backward
        optimizer.zero_grad()
        loss.backward()
        if args.net == "vgg16" or "res101":
            clip_gradient(fasterRCNN, 10.)
        optimizer.step()

        if step % args.disp_interval == 0:
            end = time.time()
            if step > 0:
                loss_temp /= (args.disp_interval + 1)# loss_temp is aver loss
            if args.mGPUs:
                loss_rpn_cls = rpn_loss_cls.mean().item()
                loss_rpn_box = rpn_loss_box.mean().item()
                loss_rcnn_cls = RCNN_loss_cls.mean().item()
                loss_rcnn_box = RCNN_loss_bbox.mean().item()
                loss_adja = adja_loss.mean().item()
                loss_adjr = adjr_loss.mean().item()
                fg_cnt = torch.sum(rois_label.ne(0))
                bg_cnt = rois_label.numel() - fg_cnt
            else:
                loss_rpn_cls = rpn_loss_cls.item()
                loss_rpn_box = rpn_loss_box.item()
                loss_rcnn_cls = RCNN_loss_cls.item()
                loss_rcnn_box = RCNN_loss_bbox.item()
                loss_adja = adja_loss.item()
                loss_adjr = adjr_loss.item()
                fg_cnt = torch.sum(rois_label.ne(0))
                bg_cnt = rois_label.numel() - fg_cnt

            print("[session %d][epoch %2d][iter %4d] loss: %.4f, lr: %.2e" \
                  % (args.session, epoch, step, loss_temp, lr))
            print("\t\t\tfg/bg=(%d/%d), time cost: %f" % (fg_cnt, bg_cnt, end-start))
            print("\t\t\trpn_cls: %.4f, rpn_box: %.4f, rcnn_cls: %.4f, rcnn_box %.4f, adja_loss %.4f, adjr_loss %.4f" \
                  % (loss_rpn_cls, loss_rpn_box, loss_rcnn_cls, loss_rcnn_box, loss_adja, loss_adjr))

            sys.stdout.flush()

            if args.use_tfboard:
                info = {
                    'loss': loss_temp,
                    'loss_rpn_cls': loss_rpn_cls,
                    'loss_rpn_box': loss_rpn_box,
                    'loss_rcnn_cls': loss_rcnn_cls,
                    'loss_rcnn_box': loss_rcnn_box,
                    'loss_adja': loss_adja,
                    'loss_adjr': loss_adjr
                }
                niter = (epoch - 1) * iters_per_epoch + step
                for tag, value in info.items():
                    writer.add_scalar(tag, value, niter)

            loss_temp = 0
            start = time.time()

    if args.mGPUs:
        save_name = os.path.join(output_dir, '{}_{}_{}_{}_{}.pth'.format(str(args.dataset), str(args.net),
                                                                         args.session, epoch, step))
        save_checkpoint({
            'session': args.session,
            'epoch': epoch + 1,
            'model': fasterRCNN.module.state_dict(),
            'optimizer': optimizer.state_dict(),
            'pooling_mode': cfg.POOLING_MODE,
            'class_agnostic': args.class_agnostic,
        }, save_name)
    else:
        save_name = os.path.join(output_dir, '{}_{}_{}_{}_{}.pth'.format(str(args.dataset), str(args.net),
                                                                         args.session, epoch, step))
        save_checkpoint({
            'session': args.session,
            'epoch': epoch + 1,
            'model': fasterRCNN.state_dict(),
            'optimizer': optimizer.state_dict(),
            'pooling_mode': cfg.POOLING_MODE,
            'class_agnostic': args.class_agnostic,
        }, save_name)
    print('save model: {}'.format(save_name))

    end = time.time()
    print(end - start)


