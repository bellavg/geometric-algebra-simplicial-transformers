import torch
import torch.nn as nn
import torch.optim as optim
import torch.nn.functional as F
from tqdm import tqdm
from algebra.cliffordalgebra import CliffordAlgebra
from models.modules.linear import MVLinear
from models.modules.gp import SteerableGeometricProductLayer
from models.modules.mvlayernorm import MVLayerNorm
from models.modules.mvsilu import MVSiLU
from models.nbody_cggnn import CEMLP
from data import nbody
import math

# Define the metric for 3D space (Euclidean)
metric = [1, 1, 1]
d = len(metric)

# Initialize the Clifford Algebra for 3D
clifford_algebra = CliffordAlgebra(metric)

def make_edge_attr(node_features, edges, batch_size):
    edge_attributes = []

    total_number_edges = edges[0].shape[0]

    # Loop over all edges
    for i in range(total_number_edges):

        node1 = edges[0][i]
        node2 = edges[1][i]

        # difference between node features
        node_i_features = node_features[node1]  # [#features(charge, loc, vel), dim]
        node_j_features = node_features[node2]  # [#features(charge, loc, vel), dim]
        difference = node_i_features - node_j_features
        edge_representation = difference
        edge_attributes.append(edge_representation)

    edge_attributes = torch.stack(edge_attributes)
    return edge_attributes

def embed_nbody_graphs(batch):
    loc, vel, edge_attr, charges, loc_end, edges = batch

    batch_size, n_nodes, _ = loc.size()

    # put the mean of the pointcloud on the origin -> translation invariant
    loc_mean = loc - loc.mean(dim=1, keepdim=True)     # [batch, nodes, dim]

    # All times do [batch, nodes, dim] -> [batch * nodes, dim]
    loc_mean = loc_mean.float().view(-1, *loc_mean.shape[2:])
    loc = loc.float().view(-1, *loc.shape[2:])
    vel = vel.float().view(-1, *vel.shape[2:])
    edge_attr = edge_attr.float().view(-1, *edge_attr.shape[2:])
    charges = charges.float().view(-1, *charges.shape[2:])
    loc_end = loc_end.float().view(-1, *loc_end.shape[2:])

    invariants = charges
    invariants = clifford_algebra.embed(invariants, (0,))

    xv = torch.stack([loc_mean, vel], dim=1) # now of the shape [batch * nodes, 2 (because its loc mean as well as vel), dim]
    covariants = clifford_algebra.embed(xv, (1, 2, 3))

    # Create a vector with [n1, n2, n3, e1, e2, e3, e4]
    nodes_in_clifford = torch.cat([invariants[:, None], covariants], dim=1) # [batch * nodes, #features(charge, loc, vel), dim]
    zero_padding_nodes = torch.zeros(5*batch_size, 4, 8)
    full_node_embedding = torch.cat((nodes_in_clifford, zero_padding_nodes), dim=1)

    batch_index = torch.arange(batch_size, device=loc_mean.device) # torch.arange(batch_size) generates a tensor from 0 to batch_size - 1, creating a sequence that represents each graph in the batch. If batch_size is 3, this tensor will be [0, 1, 2]
    edges = edges + n_nodes * batch_index[:, None, None] # creates separate edge number for every graph. so if edge for graph 1 is between 3 and 4, graph 2 will be between 8 and 9 (if n_nodes = 5)
    edges = tuple(edges.transpose(0, 1).flatten(1)) # where the first element of the tuple contains all start nodes and the second contains all end nodes for edges across the entire batch. ([edges*batch], [edges*batch])
    start_nodes, end_nodes = edges

    # Initialize an attention mask with zeros (disallow all attention initially) NO IDEA IF THIS WORKS FOR MORE IN ONE BATCH
    attention_mask = torch.zeros(25, 25)

    for b in range(batch_size):
        node_start_idx = b * (25)
        edge_start_idx = node_start_idx + 5

        for i in range(20):
            start_node = start_nodes[i].item() + node_start_idx
            end_node = end_nodes[i].item() + node_start_idx
            edge_idx = edge_start_idx + i

            # Edges can attend to their corresponding nodes
            attention_mask[edge_idx, start_node] = 1
            attention_mask[edge_idx, end_node] = 1

            # Nodes can attend to their corresponding edges
            attention_mask[start_node, edge_idx] = 1
            attention_mask[end_node, edge_idx] = 1

    # Convert the mask to float and set masked positions to -inf and allowed positions to 0
    attention_mask = attention_mask.float()
    attention_mask = attention_mask.masked_fill(attention_mask == 0, float('-inf'))
    attention_mask = attention_mask.masked_fill(attention_mask == 1, float(0.0))

    # Create a vector with [n1, n2, n3, e1, e2, e3, e4]
    orig_edge_attr_clifford = clifford_algebra.embed(edge_attr[..., None], (0,)) # now [batch * edges, 1, dim]
    extra_edge_attr_clifford = make_edge_attr(nodes_in_clifford, edges, batch_size) # [batch*edges, #numfeatures, difference + geomprod, dim]
    edge_attr_all = torch.cat((orig_edge_attr_clifford, extra_edge_attr_clifford), dim=1)
    zero_padding_edges = torch.zeros(20*batch_size, 3, 8)
    full_edge_embedding = torch.cat((zero_padding_edges, edge_attr_all), dim=1)

    loc_end_clifford = clifford_algebra.embed(loc_end, (1, 2, 3))

    return full_node_embedding, full_edge_embedding, loc_end_clifford, attention_mask
def generate_mock_batch(batch_size):
    """
    Generate a mock batch of data with specified shapes.

    Parameters:
    - batch_size (int): The size of the batch to generate.

    Returns:
    - A tuple containing tensors for loc_frame_0, vel_frame_0, edge_attr, charges, loc_frame_T, and edges.
    """
    # Constants
    n_nodes = 5  # Number of nodes
    n_features = 3  # Number of spatial features (e.g., x, y, z)
    n_edges = 20  # Number of edges
    n_edge_features = 1  # Number of features per edge

    # Generate data
    loc_frame_0 = torch.rand(batch_size, n_nodes, n_features)
    vel_frame_0 = torch.rand(batch_size, n_nodes, n_features)
    edge_attr = torch.rand(batch_size, n_edges, n_edge_features)
    charges = torch.rand(batch_size, n_nodes, 1)
    loc_frame_T = torch.rand(batch_size, n_nodes, n_features)

    # Generate edges indices
    # For simplicity, assuming all batches share the same structure of graph
    rows = torch.randint(0, n_nodes, (n_edges,))
    cols = torch.randint(0, n_nodes, (n_edges,))
    edges = torch.stack([rows, cols], dim=0).repeat(batch_size, 1, 1)  # Repeat the edge structure across the batch

    return loc_frame_0, vel_frame_0, edge_attr, charges, loc_frame_T, edges

class PositionalEncoding(nn.Module):
    def __init__(self, d_model, batch_size):
        super(PositionalEncoding, self).__init__()

        # Adding a binary feature
        # Nodes get a [1, 0] and edges get a [0, 1]
        node_marker = torch.zeros(5*batch_size, 1, 8)

        # Create a tensor of zeros with shape [20*batch_size, 1, 8] with a 1 at the beginning (onehot)
        edge_marker = torch.zeros(20 * batch_size, 1, 8)
        edge_marker[:, :, 0] = 1

        # Concatenate this new feature
        self.pe = torch.cat((node_marker, edge_marker), dim=0)

    def forward(self, x):
        print(x.shape, self.pe.shape)
        return torch.cat((x, self.pe), dim=1)

class SelfAttentionClifford(nn.Module):
    def __init__(self, num_feat, num_nodes, num_edges, algebra):
        super(SelfAttentionClifford, self).__init__()
        self.num_feat = num_feat
        self.num_nodes = num_nodes
        self.num_edges = num_edges
        self.algebra = algebra
        self.q_linear = MVLinear(algebra, num_feat, 1, subspaces=True)
        self.k_linear = MVLinear(algebra, num_feat, 1, subspaces=True)
        self.v_linear = MVLinear(algebra, num_feat, 1, subspaces=True)
        self.output_embedding = MVLinear(algebra, 1*2, num_feat, subspaces=True)

    def forward(self, feature_matrix):
        bs = feature_matrix.size(0)//25

        # Compute query, key, and value matrices
        q = self.q_linear(feature_matrix)
        k = self.k_linear(feature_matrix)
        v = self.v_linear(feature_matrix)

        # Compute dot product for attention
        q1_reshape = q.view(25*bs, -1)
        k1_reshape = k.view(25*bs, -1)

        attn = torch.mm(q1_reshape, k1_reshape.T)  # (bs*(num_nodes + num_edges), num_feat, 8)
        # Normalize the attention weights with d normally
        attn = attn / math.sqrt(k.size(-1))
        attn = F.softmax(attn, dim=-1)

        v_reshaped = v.squeeze(1)
        attention_feature_matrix = torch.matmul(attn, v_reshaped)
        attention_feature_matrix = attention_feature_matrix.unsqueeze(1)

        # Apply geometric product but might not be necessary let's check with Cong.
        gp_feature_matrix = self.geometric_product(attention_feature_matrix, attention_feature_matrix)

        concat_feature_matrix = torch.cat((attention_feature_matrix, gp_feature_matrix), dim=1)
        embed_output = self.output_embedding(concat_feature_matrix)

        return embed_output

    def geometric_product(self, a, b):
        return self.algebra.geometric_product(a, b)

class GAST_block(nn.Module):
    def __init__(self, d_model, num_heads, clifford_algebra, channels):
        super(GAST_block, self).__init__()
        self.mvlayernorm = MVLayerNorm(clifford_algebra, channels)
        self.self_attn = SelfAttentionClifford(7, 5, 20, clifford_algebra)
        # self.mvlayernorm2 = MVLayerNorm(clifford_algebra, channels)

    def forward(self, src, src_mask=None):
        src = self.mvlayernorm(src)
        src = self.self_attn(src)
        return src

class GAST(nn.Module):
    def __init__(self, num_layers, d_model, num_heads, clifford_algebra, channels):
        super(GAST, self).__init__()
        #TODO add activation function
        # self.activation = MVSiLU(clifford_algebra)
        self.layers = nn.ModuleList(
            [GAST_block(d_model, num_heads, clifford_algebra, channels) for _ in range(num_layers)])

    def forward(self, src, src_mask=None):
        for layer in self.layers:
            src = layer(src, src_mask)
        return src

class NBODY_Transformer(nn.Module):
    def __init__(self, input_dim, d_model, num_heads, num_layers, batch_size, embed_in_features, embed_out_features, clifford_algebra, channels):
        super(NBODY_Transformer, self).__init__()
        self.positional_encoding = PositionalEncoding(d_model, batch_size)
        self.GAST = GAST(num_layers, d_model, num_heads, clifford_algebra, channels)
        self.embedding = MVLinear(clifford_algebra, embed_in_features, embed_out_features, subspaces=False)

    def forward(self, nodes_in_clifford, edges_in_clifford, src_mask, batch_size):

        # POSITIONAL ENCODING left out for now
        # edges_in_clifford = self.positional_encoding(edges_in_clifford)
        src = torch.cat((nodes_in_clifford, edges_in_clifford), dim=0)

        enc_output = self.GAST(src, src_mask)
        output = enc_output

        # return only nodes and only the "pos" feature vector of the nodes
        return output[:(5*batch_size), 1, :]

# Hyperparameters
input_dim = 7  # feature_dim
d_model = 512
num_heads = 8
num_layers = 6
embed_in_features = 3
embed_out_features = 3
batch_size = 1
channels = 7
# num_samples = 3000
num_samples = 256

# Create the model
model = NBODY_Transformer(input_dim, d_model, num_heads, num_layers, batch_size, embed_in_features, embed_out_features, clifford_algebra, channels)
criterion = nn.MSELoss()
optimizer = optim.Adam(model.parameters(), lr=0.001)

demo_batch = generate_mock_batch(batch_size)
nbody_data = nbody.NBody(num_samples=num_samples, batch_size=batch_size)
train_loader = nbody_data.train_loader()
print("Data loaded")
src_mask = None

model.train()
for epoch in tqdm(range(10)):
    for batch in train_loader:
        optimizer.zero_grad()
        components = embed_nbody_graphs(batch)
        nodes, edges, tgt, attention_mask = components

        output = model(nodes, edges, src_mask=attention_mask, batch_size=batch_size)

        loss = criterion(output, tgt)
        loss.backward()
        optimizer.step()

    print(f'Epoch {epoch + 1}, Loss: {loss.item()}')
