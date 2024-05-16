import torch
import torch.nn as nn
import torch.nn.functional as F
import math
from models.modules.linear import MVLinear
from models.modules.mvlayernorm import MVLayerNorm
from models.modules.mvsilu import MVSiLU
from einops import rearrange

class SelfAttentionClifford(nn.Module):
    def __init__(self, num_feat, num_nodes, num_edges, algebra, num_heads):
        super(SelfAttentionClifford, self).__init__()
        self.num_feat = num_feat
        self.num_nodes = num_nodes
        self.num_edges = num_edges
        self.algebra = algebra
        self.num_heads = num_heads

        self.q_linear = MVLinear(algebra, num_feat, num_feat * num_heads, subspaces=True)
        self.k_linear = MVLinear(algebra, num_feat, num_feat * num_heads, subspaces=True)
        self.v_linear = MVLinear(algebra, num_feat, num_feat * num_heads, subspaces=True)
        self.output_embedding = MVLinear(algebra, num_feat*num_heads, num_feat, subspaces=True)
        self.concat_layernorm = MVLayerNorm(algebra, 2) #TODO TBD

    def forward(self, feature_matrix, attention_mask):
        # bs = feature_matrix.size(0)//25

        bs = feature_matrix.size(0) // (self.num_nodes + self.num_edges)
        # Compute query, key, and value matrices
        q = self.q_linear(feature_matrix)
        k = self.k_linear(feature_matrix)
        v = self.v_linear(feature_matrix)

        # Compute dot product for attention

        q = rearrange(self.q_linear(feature_matrix), '(bs n) (h d) c -> bs h n (d c)', bs=bs,
                      n=self.num_nodes + self.num_edges, h=self.num_heads, d=self.num_feat)
        k = rearrange(self.k_linear(feature_matrix), '(bs n) (h d) c -> bs h n (d c)', bs=bs,
                      n=self.num_nodes + self.num_edges, h=self.num_heads, d=self.num_feat)
        v = rearrange(self.v_linear(feature_matrix), '(bs n) (h d) c -> bs h n (d c)', bs=bs,
                      n=self.num_nodes + self.num_edges, h=self.num_heads, d=self.num_feat)


        q = q / math.sqrt(self.num_feat * 8)  # Scale by sqrt(d_k * 8) 8 from CLIFFORD

        # attn = torch.matmul(q, k.T)  # (bs*(num_nodes + num_edges), num_feat, 8)
        attn = torch.matmul(q, k.transpose(-2, -1))

        attention_mask = attention_mask.unsqueeze(1).repeat(1, self.num_heads, 1,1)
        attn = attn + attention_mask  # Apply the mask
        attn = F.softmax(attn, dim=-1)

        attention_output = torch.matmul(attn, v) # [batch_size, num_heads, n_nodes + n_edges, d_model, 8]


        attention_output = attention_output.transpose(1, 2).contiguous().view(bs*(self.num_nodes + self.num_edges), self.num_heads * self.num_feat, 8)

        # v_reshaped = v.squeeze(1)

        # attention_feature_matrix = torch.matmul(attn, v_reshaped)
        # attention_feature_matrix = attention_feature_matrix.unsqueeze(1)


        gp_feature_matrix = self.geometric_product(attention_output, attention_output)

        concat_feature_matrix = torch.cat((attention_output, gp_feature_matrix), dim=1)
        normalized_concat_feature_matrix = self.concat_layernorm(concat_feature_matrix)
        embed_output = self.output_embedding(normalized_concat_feature_matrix)


        #
        # # Apply geometric product but might not be necessary let's check with Cong.
        # gp_feature_matrix = self.geometric_product(attention_feature_matrix, attention_feature_matrix)
        #
        # concat_feature_matrix = torch.cat((attention_feature_matrix, gp_feature_matrix), dim=1)
        # normalized_concat_feature_matrix = self.concat_layernorm(concat_feature_matrix)
        # embed_output = self.output_embedding(normalized_concat_feature_matrix)

        # embed_output = self.output_embedding(concat_feature_matrix)

        return embed_output

    def geometric_product(self, a, b):
        return self.algebra.geometric_product(a, b)
#



# class SelfAttentionClifford(nn.Module):
#     def __init__(self, num_feat, num_nodes, num_edges, algebra, num_heads):
#         super(SelfAttentionClifford, self).__init__()
#         self.num_feat = num_feat
#         self.num_nodes = num_nodes
#         self.num_edges = num_edges
#         self.algebra = algebra
#         self.num_heads = num_heads
#
#
#         self.q_linear = MVLinear(algebra, num_feat, 1, subspaces=True)
#         self.k_linear = MVLinear(algebra, num_feat, 1, subspaces=True)
#         self.v_linear = MVLinear(algebra, num_feat, 1, subspaces=True)
#         self.output_embedding = MVLinear(algebra, 1*2, num_feat, subspaces=True)
#         self.concat_layernorm = MVLayerNorm(algebra, 2)
#
#     def forward(self, feature_matrix, attention_mask):
#         bs = feature_matrix.size(0) // (self.num_nodes + self.num_edges)
#
#         # Compute query, key, and value matrices
#         q = self.q_linear(feature_matrix)
#         k = self.k_linear(feature_matrix)
#         v = self.v_linear(feature_matrix)
#
#         # Compute dot product for attention
#         q1_reshape = q.view(25*bs, -1)
#         k1_reshape = k.view(25*bs, -1)
#
#         attn = torch.mm(q1_reshape, k1_reshape.T)  # (bs*(num_nodes + num_edges), num_feat, 8)
#         # Normalize the attention weights with d normally
#         attn = attn / math.sqrt(k.size(-1))
#         if attention_mask is not None:
#             attn = attn + attention_mask
#
#         attn = F.softmax(attn, dim=-1)
#
#         v_reshaped = v.squeeze(1)
#         attention_feature_matrix = torch.matmul(attn, v_reshaped)
#         attention_feature_matrix = attention_feature_matrix.unsqueeze(1)
#
#         # Apply geometric product but might not be necessary let's check with Cong.
#         gp_feature_matrix = self.geometric_product(attention_feature_matrix, attention_feature_matrix)
#
#         concat_feature_matrix = torch.cat((attention_feature_matrix, gp_feature_matrix), dim=1)
#         normalized_concat_feature_matrix = self.concat_layernorm(concat_feature_matrix)
#         embed_output = self.output_embedding(normalized_concat_feature_matrix)
#
#         # embed_output = self.output_embedding(concat_feature_matrix)
#
#         return embed_output
#
#     def geometric_product(self, a, b):
#         return self.algebra.geometric_product(a, b)

# class SelfAttentionClifford(nn.Module):
#     def __init__(self, num_feat, num_nodes, num_edges, algebra, num_heads):
#         super(SelfAttentionClifford, self).__init__()
#         self.num_feat = num_feat
#         self.num_nodes = num_nodes
#         self.num_edges = num_edges
#         self.num_heads = num_heads
#         self.algebra = algebra
#
#         self.q_linear = nn.ModuleList([MVLinear(algebra, num_feat, 1, subspaces=True) for _ in range(num_heads)])
#         self.k_linear = nn.ModuleList([MVLinear(algebra, num_feat, 1, subspaces=True) for _ in range(num_heads)])
#         self.v_linear = nn.ModuleList([MVLinear(algebra, num_feat, 1, subspaces=True) for _ in range(num_heads)])
#         self.output_embedding = MVLinear(algebra, num_heads * 2, num_feat, subspaces=True)
#         self.concat_layernorm = MVLayerNorm(algebra, num_heads * 2)
#
#     def forward(self, feature_matrix, attention_mask):
#         bs = feature_matrix.size(0) // (self.num_nodes + self.num_edges)
#         head_outputs = []
#
#         for i in range(self.num_heads):
#             # Compute query, key, and value matrices for each head
#             q = self.q_linear[i](feature_matrix)
#             k = self.k_linear[i](feature_matrix)
#             v = self.v_linear[i](feature_matrix)
#
#             # Compute dot product for attention
#             q1_reshape = q.view(25*bs, -1)
#             k1_reshape = k.view(25*bs, -1)
#
#             attn = torch.mm(q1_reshape, k1_reshape.T)  # (bs*(num_nodes + num_edges), num_feat, 8)
#             # Normalize the attention weights with d normally
#             attn = attn / math.sqrt(k.size(-1))
#             if attention_mask is not None:
#                 attn = attn + attention_mask
#
#             attn = F.softmax(attn, dim=-1)
#
#             v_reshaped = v.squeeze(1)
#             attention_feature_matrix = torch.matmul(attn, v_reshaped)
#             attention_feature_matrix = attention_feature_matrix.unsqueeze(1)
#
#             # Apply geometric product but might not be necessary let's check with Cong.
#             gp_feature_matrix = self.geometric_product(attention_feature_matrix, attention_feature_matrix)
#
#             concat_feature_matrix = torch.cat((attention_feature_matrix, gp_feature_matrix), dim=1)
#             head_outputs.append(concat_feature_matrix)
#
#         # Concatenate the outputs from all heads
#         multi_head_concat = torch.cat(head_outputs, dim=1)
#         normalized_concat_feature_matrix = self.concat_layernorm(multi_head_concat)
#         embed_output = self.output_embedding(normalized_concat_feature_matrix)
#
#         return embed_output
#
#     def geometric_product(self, a, b):
#         return self.algebra.geometric_product(a, b)
class GAST_block(nn.Module):
    def __init__(self, clifford_algebra, channels, num_nodes, num_edges, num_heads):
        super(GAST_block, self).__init__()
        self.mvlayernorm = MVLayerNorm(clifford_algebra, channels)
        self.self_attn = SelfAttentionClifford(channels, num_nodes, num_edges, clifford_algebra, num_heads)

    def forward(self, src, src_mask):
        src_norm = self.mvlayernorm(src)
        src_attn = self.self_attn(src_norm, src_mask)
        src = src + src_attn # TODO Residual connection BUT ADD OR MVlinear
        return src

class GAST(nn.Module):
    def __init__(self, clifford_algebra, channels, num_nodes, num_edges, num_layers, num_heads):
        super(GAST, self).__init__()
        self.activation = MVSiLU(clifford_algebra, channels)
        self.layers = nn.ModuleList(
            [GAST_block(clifford_algebra, channels, num_nodes, num_edges, num_heads) for _ in range(num_layers)]
        )

    def forward(self, src, src_mask=None):
        for layer in self.layers:
            src = layer(src, src_mask)
            src = self.activation(src)
        return src
