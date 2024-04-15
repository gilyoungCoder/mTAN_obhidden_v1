import math
import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np
from setmodels import SetTransformer
import random

class create_classifier(nn.Module):
 
    def __init__(self, latent_dim, nhidden=16, N=2):
        super(create_classifier, self).__init__()
        self.gru_rnn = nn.GRU(latent_dim, nhidden, batch_first=True)
        self.classifier = nn.Sequential(
            nn.Linear(nhidden, 300),
            nn.ReLU(),
            nn.Linear(300, 300),
            nn.ReLU(),
            nn.Linear(300, N))
        
       
    def forward(self, z):
        _, out = self.gru_rnn(z)
        return self.classifier(out.squeeze(0))
    

class multiTimeAttention(nn.Module):
    
    def __init__(self, input_dim, nhidden=16, 
                 embed_time=16, num_heads=1):
        super(multiTimeAttention, self).__init__()
        assert embed_time % num_heads == 0
        self.embed_time = embed_time
        self.embed_time_k = embed_time // num_heads
        self.h = num_heads
        self.dim = input_dim
        self.nhidden = nhidden
        self.linears = nn.ModuleList([nn.Linear(embed_time, embed_time), 
                                      nn.Linear(embed_time, embed_time),
                                      nn.Linear(input_dim*num_heads, nhidden)])
        
    def attention(self, query, key, value, mask=None, dropout=None):
        "Compute 'Scaled Dot Product Attention'"
        dim = value.size(-1)
        d_k = query.size(-1)
        scores = torch.matmul(query, key.transpose(-2, -1)) \
                 / math.sqrt(d_k)
        scores = scores.unsqueeze(-1).repeat_interleave(dim, dim=-1)
        # scores : 50 x 1 x 128 x 203 x 82
#        print(f"score : {scores.shape}")
        if mask is not None:
            # mask : 50 x 1 x 1 x 203 x 82
            scores = scores.masked_fill(mask.unsqueeze(-3) == 0, -1e9)
        p_attn = F.softmax(scores, dim = -2)
        # print(f"p_attn, {p_attn.shape}")
        # p_attn 50 x 1 x 128 x 203 x 82
        if dropout is not None:
            p_attn = dropout(p_attn)
        # value 50 x 1 x 1 x 203 x 82
        # return 50 x 1 x 128 x 82
        # print("attention")
        # print(torch.sum(p_attn*value.unsqueeze(-3), -2).shape)
        return torch.sum(p_attn*value.unsqueeze(-3), -2), p_attn
    
    
    def forward(self, query, key, value, mask=None, dropout=None):
        "Compute 'Scaled Dot Product Attention'"
        batch, seq_len, dim = value.size()
        if mask is not None:
            # Same mask applied to all h heads.
            # mask : 50 x 203 x 82 => [50, 1, 203, 82]
            mask = mask.unsqueeze(1)
        value = value.unsqueeze(1)
        # query : 1 x 1 x 128 x 128, key: 50 x 1 x 203 x embed_time(128)
        query, key = [l(x).view(x.size(0), -1, self.h, self.embed_time_k).transpose(1, 2)
                      for l, x in zip(self.linears, (query, key))]
        x, _ = self.attention(query, key, value, mask, dropout)
        x = x.transpose(1, 2).contiguous() \
             .view(batch, -1, self.h * dim)
        # 50 x 128 x 82
        return self.linears[-1](x)
    
    
class TimeSeriesAugmentation(nn.Module):
    def __init__(self, input_dim, hidden_dim, output_dim, num_outputs):
        super(TimeSeriesAugmentation, self).__init__()
        # 숨겨진 표현을 추출하기 위한 초기 변환 레이어
        # self.initial_transform = nn.Linear(input_dim, hidden_dim)
        self.initial_transform = nn.Sequential(
            nn.Linear(input_dim, 300),
            nn.ReLU(),
            nn.Linear(300, hidden_dim))
        self.dim = output_dim
        # Set Transformer 모델
        self.set_transformer = SetTransformer(dim_input=hidden_dim, num_outputs=num_outputs, dim_output=hidden_dim)
        
        # 증폭된 숨겨진 표현을 (t, x) 형식으로 변환하기 위한 레이어
        # self.final_transform = nn.Linear(hidden_dim, output_dim)
        self.final_transform = nn.Sequential(
            nn.Linear(128, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, output_dim))
        # self.final_transform_observed = nn.Sequential(
        #     nn.Linear(128, hidden_dim),
        #     nn.ReLU(),
        #     nn.Linear(hidden_dim, output_dim-1))
        self.sigmoid = nn.Sigmoid()

    def forward(self, t, x):
        # t와 x를 concatenate하여 초기 변환 레이어에 입력
        tx = torch.cat([x, t.unsqueeze(-1)], dim=-1)
        hidden_representation = self.initial_transform(tx)
        # hidden_representation = tx
        # Set Transformer를 사용하여 숨겨진 표현 증폭
        augmented_representation, observed_representation = self.set_transformer(hidden_representation)
        # augmented_representation = self.set_transformer(tx)
        
        # 증폭된 숨겨진 표현을 (t, x) 형식으로 변환
        augmented_out = self.final_transform(augmented_representation)
        # augmented_out = augmented_representation
        output = self.sigmoid(augmented_out)
        # output = augmented_out

        # observed_out = self.final_transform_observed(observed_representation)
        observed_out = self.final_transform(observed_representation)
        ob_output = self.sigmoid(observed_out)
        # ob_output = observed_out
        # ob_x = ob_output
        ob_x, ob_t = ob_output[ :, :, :self.dim-1], ob_output[ :, :, -1]

        # 새로운 t와 x 분리
        new_x, new_t = output[ :, :, :self.dim-1], output[ :, :, -1]
        # new_t = self.sigmoid(new_t)
        return new_x, new_t, ob_x, t
        # return torch.cat((new_x, ob_x), -2), torch.cat((new_t, t), -1)


    
class enc_mtan_rnn(nn.Module):
    def __init__(self, input_dim, query, latent_dim=2, nhidden=16, 
                 embed_time=16, num_heads=1, learn_emb=False, device='cuda'):
        super(enc_mtan_rnn, self).__init__()
        self.embed_time = embed_time
        self.dim = input_dim
        self.device = device
        self.nhidden = nhidden
        self.query = query
        self.learn_emb = learn_emb
        self.att = multiTimeAttention(input_dim, nhidden, embed_time, num_heads)
        self.gru_rnn = nn.GRU(nhidden, nhidden, bidirectional=True, batch_first=True)

        self.hiddens_to_z0 = nn.Sequential(
            nn.Linear(2*nhidden, 50),
            nn.ReLU(),
            nn.Linear(50, latent_dim * 2))

        if learn_emb:
            self.periodic = nn.Linear(1, embed_time-1)
            self.linear = nn.Linear(1, 1)
        
    
    def learn_time_embedding(self, tt):
        tt = tt.to(self.device)
        tt = tt.unsqueeze(-1)
        ## print(f"tt: {tt.shape}") tt: torch.Size([50, 203, 1]) tt: torch.Size([1, 128, 1])
        out2 = torch.sin(self.periodic(tt))
        out1 = self.linear(tt)
        return torch.cat([out1, out2], -1)
    
    def fixed_time_embedding(self, pos):
        d_model=self.embed_time
        pe = torch.zeros(pos.shape[0], pos.shape[1], d_model)
        position = 48.*pos.unsqueeze(2)
        div_term = torch.exp(torch.arange(0, d_model, 2) *
                             -(np.log(10.0) / d_model))
        pe[:, :, 0::2] = torch.sin(position * div_term)
        pe[:, :, 1::2] = torch.cos(position * div_term)
        return pe
       
    def forward(self, x, t):

        time_steps = t.cpu()
        
        # x_aug_copy = x_aug.clone()
        # dim = x.size(2)//2
        # mask = x[:, :, dim:]
        # mask = torch.cat((mask, mask), 2)        
        if self.learn_emb:
            key = self.learn_time_embedding(time_steps).to(self.device)
            query = self.learn_time_embedding(self.query.unsqueeze(0)).to(self.device)
            ## tp : 50(batch) x 203 / query.unsqueuze(0) : 1 x 128
        else:
            key = self.fixed_time_embedding(time_steps).to(self.device)
            query = self.fixed_time_embedding(self.query.unsqueeze(0)).to(self.device)
        
        # print("tt: ", t)
        # print("mask: ", mask.shape, mask[0, :, 0])
        out = self.att(query, key, x)
        out, _ = self.gru_rnn(out)
        out = self.hiddens_to_z0(out)
        return out
    
    
class dec_mtan_rnn(nn.Module):
 
    def __init__(self, input_dim, query, latent_dim=2, nhidden=16, 
                 embed_time=16, num_heads=1, learn_emb=False, device='cuda'):
        super(dec_mtan_rnn, self).__init__()
        self.embed_time = embed_time
        self.dim = input_dim
        self.device = device
        self.nhidden = nhidden
        self.query = query
        self.learn_emb = learn_emb
        self.att = multiTimeAttention(2*nhidden, 2*nhidden, embed_time, num_heads)
        self.gru_rnn = nn.GRU(latent_dim, nhidden, bidirectional=True, batch_first=True)    
        self.z0_to_obsh = nn.Sequential(
            nn.Linear(2*nhidden, 50),
            nn.ReLU(),
            nn.Linear(50, input_dim))
        self.set_trans = TimeSeriesAugmentation(input_dim+1, 256, input_dim+1, num_outputs=1)
        self.obsh_to_obs = nn.Sequential(
            nn.Linear(input_dim, 50),
            nn.ReLU(),
            nn.Linear(50, input_dim))
        if learn_emb:
            self.periodic = nn.Linear(1, embed_time-1)
            self.linear = nn.Linear(1, 1)
        
        
    def learn_time_embedding(self, tt):
        tt = tt.to(self.device)
        tt = tt.unsqueeze(-1)
        out2 = torch.sin(self.periodic(tt))
        out1 = self.linear(tt)
        return torch.cat([out1, out2], -1)
        
        
    def fixed_time_embedding(self, pos):
        d_model = self.embed_time
        pe = torch.zeros(pos.shape[0], pos.shape[1], d_model)
        position = 48.*pos.unsqueeze(2)
        div_term = torch.exp(torch.arange(0, d_model, 2) *
                             -(np.log(10.0) / d_model))
        pe[:, :, 0::2] = torch.sin(position * div_term)
        pe[:, :, 1::2] = torch.cos(position * div_term)
        return pe
       
    def forward(self, z, time_steps):
        out, _ = self.gru_rnn(z)
        time_steps = time_steps.cpu()
        if self.learn_emb:
            query = self.learn_time_embedding(time_steps).to(self.device)
            key = self.learn_time_embedding(self.query.unsqueeze(0)).to(self.device)
        else:
            query = self.fixed_time_embedding(time_steps).to(self.device)
            key = self.fixed_time_embedding(self.query.unsqueeze(0)).to(self.device)
        out = self.att(query, key, out)
        out = self.z0_to_obsh(out)
        # _, _, out, _ = self.set_trans(time_steps.to(self.device), out)
        # out = self.obsh_to_obs(out)
        return out        