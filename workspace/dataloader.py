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