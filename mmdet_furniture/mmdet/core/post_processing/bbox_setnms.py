import torch
from mmdet.ops.nms import nms_wrapper
import numpy as np


def set_nms(multi_bboxes,
                   multi_scores,
                   multi_roiidxs,
                   score_thr,  # default 0.05 in crowddet
                   nms_cfg,  # nms=dict(type='setnms', iou_thr=0.5)
                   max_num=-1,
                   score_factors=None):
    """NMS for multi-class bboxes.

    Args:
        multi_bboxes (Tensor): shape (n, #class*4) or (n, 4)
        multi_scores (Tensor): shape (n, #class), where the 0th column
            contains scores of the background class, but this will be ignored.
        score_thr (float): bbox threshold, bboxes with scores lower than it
            will not be considered.
        nms_thr (float): NMS IoU threshold
        max_num (int): if there are more than max_num bboxes after NMS,
            only top max_num will be kept.
        score_factors (Tensor): The factors multiplied to scores before
            applying NMS

    Returns:
        tuple: (bboxes, labels), tensors of shape (k, 5) and (k, 1). Labels
            are 0-based.
    """
    num_classes = multi_scores.size(1) - 1
    assert num_classes == 1
    # exclude background category
    if multi_bboxes.shape[1] > 4:
        bboxes = multi_bboxes.view(multi_scores.size(0), -1, 4)[:, 1:]  # [n, nb_class, 4]
    else:
        bboxes = multi_bboxes[:, None].expand(-1, num_classes, 4)  # [n, nb_class, 4]
    scores = multi_scores[:, 1:]  # NOTE  # [n,nb_class]
    multi_roiidxs = multi_roiidxs[:, 1:]

    # filter out boxes with low scores
    valid_mask = scores > score_thr
    bboxes = bboxes[valid_mask]  # [nb_valid,4]
    if score_factors is not None:
        scores = scores * score_factors[:, None]
    scores = scores[valid_mask]  # 1-D tensor, [nb_valid]
    labels = valid_mask.nonzero()[:, 1]  # [nb_valid]
    multi_roiidxs = multi_roiidxs[valid_mask]
    assert labels.eq(0).nonzero().numel() == labels.size(0)  # NOTE
 
    if bboxes.numel() == 0:
        bboxes = multi_bboxes.new_zeros((0, 5))
        labels = multi_bboxes.new_zeros((0, ), dtype=torch.long)
        return bboxes, labels

    # Modified from https://github.com/pytorch/vision/blob
    # /505cd6957711af790211896d32b40291bea1bc21/torchvision/ops/boxes.py#L39.
    # strategy: in order to perform NMS independently per class.
    # we add an offset to all the boxes. The offset is dependent
    # only on the class idx, and is large enough so that boxes
    # from different classes do not overlap
    # max_coordinate = bboxes.max()
    # offsets = labels.to(bboxes) * (max_coordinate + 1)  # NOTE 
    # bboxes_for_nms = bboxes + offsets[:, None]
    nms_cfg_ = nms_cfg.copy()
    nms_type = nms_cfg_.pop('type', 'nms')
    assert nms_type == 'set_nms'

    # nms_op = getattr(nms_wrapper, nms_type)
    # dets, keep = nms_op(torch.cat([bboxes_for_nms, scores[:, None]], 1), **nms_cfg_)
    bboxes_for_nms = np.concatenate((bboxes.cpu().numpy(), scores.cpu().numpy()[:,None], 
        multi_roiidxs.cpu().numpy()[:,None]), axis=1)
    keep = set_nms_wrapper(bboxes_for_nms, **nms_cfg_)  # [n,6]
    keep = torch.from_numpy(np.array(keep)).to(bboxes.device)
    bboxes = bboxes[keep]
    # scores = dets[:, -1]  # soft_nms will modify scores # NOTE
    scores = scores[keep]  
    labels = labels[keep]
    assert labels.eq(0).nonzero().numel() == labels.size(0)  # NOTE

    if keep.size(0) > max_num:
        _, inds = scores.sort(descending=True)
        inds = inds[:max_num]
        bboxes = bboxes[inds]
        scores = scores[inds]
        labels = labels[inds]

    return torch.cat([bboxes, scores[:, None]], 1), labels


def set_nms_wrapper(dets, iou_thr):
    x1 = dets[:, 0]
    y1 = dets[:, 1]
    x2 = dets[:, 2]
    y2 = dets[:, 3]
    scores = dets[:, 4]
    set_index = dets[:, 5]

    areas = (x2 - x1 + 1) * (y2 - y1 + 1)
    order = scores.argsort()[::-1]

    keep = []
    while order.size > 0:
        i = order[0]
        set_i = set_index[i]
        keep.append(i)
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])

        w = np.maximum(0.0, xx2 - xx1 + 1)
        h = np.maximum(0.0, yy2 - yy1 + 1)
        inter = w * h
        ovr = inter / (areas[i] + areas[order[1:]] - inter)

        inds = np.where((ovr <= iou_thr)|((ovr > iou_thr) & (set_index[order[1:]] == set_i)))[0]
        order = order[inds + 1]
    return keep