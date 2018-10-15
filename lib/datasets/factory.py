# --------------------------------------------------------
# Fast R-CNN
# Copyright (c) 2015 Microsoft
# Licensed under The MIT License [see LICENSE for details]
# Written by Ross Girshick
# --------------------------------------------------------

"""Factory method for easily getting imdbs by name."""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

__sets = {}

from datasets.coco import coco
from datasets.vg import vg
from datasets.ade import ade
from datasets.pascal_voc import pascal_voc

import numpy as np

# # Set up voc_<year>_<split>
for year in ['2007', '2012']:
  for split in ['train', 'val', 'trainval', 'test']:
    name = 'voc_{}_{}'.format(year, split)
    __sets[name] = (lambda split=split, year=year: pascal_voc(split, year))
# #
# for year in ['2014']:
#   for split in ['train', 'val', 'minival', 'valminusminival', 'trainval']:
#     name = 'coco_{}_{}'.format(year, split)
#     __sets[name] = (lambda split=split, year=year: coco(split, year))
# #
# for year in ['2017']:
#   for split in ['train', 'val']:
#     name = 'coco_{}_{}'.format(year, split)
#     __sets[name] = (lambda split=split, year=year: coco(split, year))
#
for split in ['train', 'val', 'train_big', 'val_big']:
    name = 'vg_{}'.format(split)
    __sets[name] = (lambda split=split: vg(split))

# Set up ade_<split>_5
for split in ['train', 'val', 'mval', 'mtest']:
    name = 'ade_{}_5'.format(split)
    __sets[name] = (lambda split=split: ade(split))
        
def get_imdb(name):
  """Get an imdb (image database) by name."""
  if name not in __sets:
    raise KeyError('Unknown dataset: {}'.format(name))
  return __sets[name]()


def list_imdbs():
  """List all registered imdbs."""
  return list(__sets.keys())
