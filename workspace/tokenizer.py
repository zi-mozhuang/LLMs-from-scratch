import re
import tiktoken

from dataloader import SimpleTokenizerV1

with open("the-verdict.txt", "r", encoding="utf-8") as f:
    raw_text = f.read()
print("Total number of characters:", len(raw_text))

preprocessed = re.split(r'([,.:;?_!"()\']|--|\s)', raw_text)
preprocessed = [item.strip() for item in preprocessed if item.strip()]
print("Total number of tokens:", len(preprocessed))

all_tokens = sorted(set(preprocessed))
all_tokens.extend(["<unk>", "<endoftext>"])
print("Vocabulary size:", len(all_tokens))

vocab = {token:integer for integer,token in enumerate(all_tokens)}
for i, item in enumerate(vocab.items()):
    print(item)
    if i >= 5:
        break
for i, item in enumerate(list(vocab.items())[-5:]):
    print(item)

tokenizer = tiktoken.get_encoding("gpt2")
text = (
    "Hello, do you like tea? <|endoftext|> In the sunlit terraces"
     "of someunknownPlace."
)
integers = tokenizer.encode(text, allowed_special={"<|endoftext|>"})
print(integers)
decoded = tokenizer.decode(integers)
print(decoded)

tokenizer = tiktoken.get_encoding("gpt2")
with open("the-verdict.txt", "r", encoding="utf-8") as f:
    raw_text = f.read()
enc_text = tokenizer.encode(raw_text)
print(len(enc_text))

enc_sample = enc_text[50:]
context_size = 4 
for i in range(1, context_size+1):
    context = enc_sample[:i]
    desired = enc_sample[i]
    print(tokenizer.decode(context), "---->", tokenizer.decode([desired]))
