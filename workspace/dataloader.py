import re

class SimpleTokenizerV1:
    def __init__(self, vocab):
        self.str_to_int = vocab
        self.int_to_str = {v:k for k,v in vocab.items()}
    
    def encode(self, text):
        tokens = re.split(r'([,.:;?_!"()\']|--|\s)', text)
        tokens = [temp for item in tokens if (temp:= item.strip())]
        tokens = [token if token in self.str_to_int else "<unk>" for token in tokens]
        return [self.str_to_int[token] for token in tokens]
    
    def decode(self, ids):
        text = " ".join([self.int_to_str[id] for id in ids])
        return re.sub(r'([,.:;?_!"()\']|--|\s)', r'\1', text)

import torch
from torch.utils.data import Dataset, DataLoader

class GPTDataset(Dataset):
    def __init__(self, data, tokenizer, max_length, stride):
        self.input_ids = []
        self.target_ids = []
        token_ids = tokenizer.encode(data)
        for i in range(0, len(token_ids) - max_length, stride):
            input_chunk = token_ids[i:i + max_length]
            target_chunk = token_ids[i + 1:i + max_length + 1]
            self.input_ids.append(torch.tensor(input_chunk))
            self.target_ids.append(torch.tensor(target_chunk))

    def __len__(self):
        return len(self.input_ids)
    
    def __getitem__(self, index):
        return self.input_ids[index], self.target_ids[index]