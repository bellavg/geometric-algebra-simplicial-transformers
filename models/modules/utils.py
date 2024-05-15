""""
This code is from David Ruhe's Clifford Group Equivariant Neural Networks repository:
https://github.com/DavidRuhe/clifford-group-equivariant-neural-networks
"""

import torch


def unsqueeze_like(tensor: torch.Tensor, like: torch.Tensor, dim=0):
    """
    Unsqueeze last dimensions of tensor to match another tensor's number of dimensions.

    Args:
        tensor (torch.Tensor): tensor to unsqueeze
        like (torch.Tensor): tensor whose dimensions to match
        dim: int: starting dim, default: 0.
    """
    n_unsqueezes = like.ndim - tensor.ndim
    if n_unsqueezes < 0:
        raise ValueError(f"tensor.ndim={tensor.ndim} > like.ndim={like.ndim}")
    elif n_unsqueezes == 0:
        return tensor
    else:
        return tensor[dim * (slice(None),) + (None,) * n_unsqueezes]
