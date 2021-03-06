import torch.nn as nn
import torch, sys
import math
import torch.utils.model_zoo as model_zoo

from torch.autograd import Variable
from torch.nn import Parameter
import resnet as rn
from other_utils import *


class GraphConvolution(nn.Module):
	"""
	Simple GCN layer, similar to https://arxiv.org/abs/1609.02907
	"""
	
	def __init__(self, in_features, out_features, bias=False):
		
		super(GraphConvolution, self).__init__()
		self.in_features = in_features  
		self.out_features = out_features  
		self.weight = Parameter(torch.Tensor(in_features, out_features))  
		if bias:
			self.bias = Parameter(torch.Tensor(1, 1, out_features))
		else:
			self.register_parameter('bias', None)
		self.reset_parameters()
	
	def reset_parameters(self):
		
		stdv = 1. / math.sqrt(self.weight.size(1))
		self.weight.data.uniform_(-stdv, stdv)
		if self.bias is not None:
			self.bias.data.uniform_(-stdv, stdv)
	
	def forward(self, input, adj):
		support = torch.matmul(input, self.weight)  
		output = torch.matmul(adj, support)  
		if self.bias is not None:
			return output + self.bias
		else:
			return output
	
	def __repr__(self):
		
		return self.__class__.__name__ + ' (' \
			   + str(self.in_features) + ' -> ' \
			   + str(self.out_features) + ')'
	

class GCNResnet(nn.Module):

	def __init__(self, block, layers, num_labels=14,in_channel=300, adj_file=None, tao=0.4, p=0.15):
		self.inplanes = 64
		self.num_labels = num_labels
		super(GCNResnet, self).__init__()
		self.conv1 = nn.Conv2d(3, 64, kernel_size=7, stride=2, padding=3,
							   bias=False)
		self.bn1 = nn.BatchNorm2d(64)
		self.relu = nn.ReLU(inplace=True)
		self.maxpool = nn.MaxPool2d(kernel_size=3, stride=2, padding=1)
		self.layer1 = self._make_layer(block, 64, layers[0])
		self.layer2 = self._make_layer(block, 128, layers[1], stride=2)
		self.layer3 = self._make_layer(block, 256, layers[2], stride=2)
		self.layer4 = self._make_layer(block, 512, layers[3], stride=2)
		self.avgpool = nn.AdaptiveAvgPool2d(1)

		self.gc1 = GraphConvolution(in_channel, 1024)
		self.gc2 = GraphConvolution(1024, 2048)
		self.relu = nn.LeakyReLU(0.2)
		_adj = gen_A(p, num_labels, tao, adj_file)
		self.A = Parameter(torch.from_numpy(_adj).float())

		for m in self.modules():
			if isinstance(m, nn.Conv2d):
				n = m.kernel_size[0] * m.kernel_size[1] * m.out_channels
				m.weight.data.normal_(0, math.sqrt(2. / n))
			elif isinstance(m, nn.BatchNorm2d):
				m.weight.data.fill_(1)
				m.bias.data.zero_()

	def _make_layer(self, block, planes, blocks, stride=1):
		downsample = None
		if stride != 1 or self.inplanes != planes * block.expansion:
			downsample = nn.Sequential(
				nn.Conv2d(self.inplanes, planes * block.expansion,
						  kernel_size=1, stride=stride, bias=False),
				nn.BatchNorm2d(planes * block.expansion),
			)

		layers = []
		layers.append(block(self.inplanes, planes, stride, downsample))
		self.inplanes = planes * block.expansion
		for i in range(1, blocks):
			layers.append(block(self.inplanes, planes))

		return nn.Sequential(*layers)

	def forward(self, x, inp):
		inp = inp[0].cuda() if torch.cuda.is_available() else inp[0]
		adj = gen_adj(self.A).detach().cuda() if torch.cuda.is_available() else  gen_adj(self.A).detach()
		g_weights = self.gc1(inp, adj)  
		g_weights = self.relu(g_weights)  
		g_weights = self.gc2(g_weights, adj)  
		
		x = self.conv1(x)
		x = self.bn1(x)
		x = self.relu(x)
		x = self.maxpool(x)

		x = self.layer1(x)
		x = self.layer2(x)
		x = self.layer3(x)
		x = self.layer4(x)
		feat = x			
		N, C, H, W = feat.shape		
		x = self.avgpool(x)	
		x = x.view(x.size(0), -1)	
		y = torch.matmul(x, torch.transpose(g_weights, 0, 1))

		g_weights = g_weights.view(1, self.num_labels, C, 1, 1)	
		fc_weights = g_weights 

		feat = feat.unsqueeze(1)	
		hm = feat * fc_weights		
		hm = hm.sum(2)		
		heatmap = hm

		return y, heatmap
	
	def get_config_optim(self, lr, scale = 10.0):
		return [
			{'params': self.conv1.parameters(), 'lr': lr},
			{'params': self.bn1.parameters(), 'lr': lr},
			{'params': self.relu.parameters(), 'lr': lr},
			{'params': self.maxpool.parameters(), 'lr': lr},
			{'params': self.layer1.parameters(), 'lr': lr},
			{'params': self.layer2.parameters(), 'lr': lr},
			{'params': self.layer3.parameters(), 'lr': lr},
			{'params': self.layer4.parameters(), 'lr': lr},
			{'params': self.gc1.parameters(), 'lr': lr * scale},
			{'params': self.gc2.parameters(), 'lr': lr * scale},
		]
	

def grn50(pretrained=False, **kwargs):

	model = GCNResnet(rn.Bottleneck, [3, 4, 6, 3], **kwargs)
	if pretrained:
		pretrained_dict = model_zoo.load_url(rn.model_urls['resnet50'])
		model_dict = model.state_dict()
		pretrained_dict = {k:v for k,v in pretrained_dict.items() if k in model_dict}
		model_dict.update(pretrained_dict)
		model.load_state_dict(model_dict)
	return model


def grn101(pretrained=False, **kwargs):

	model = GCNResnet(rn.Bottleneck, [3, 4, 23, 3], **kwargs)
	if pretrained:
		pretrained_dict = model_zoo.load_url(rn.model_urls['resnet101'])
		model_dict = model.state_dict()
		pretrained_dict = {k:v for k,v in pretrained_dict.items() if k in model_dict}
		model_dict.update(pretrained_dict)
		model.load_state_dict(model_dict)
	return model
