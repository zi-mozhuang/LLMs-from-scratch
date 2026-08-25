import re
import tiktoken

tokenizer = tiktoken.get_encoding("gpt2")
with open("the-verdict.txt", "r", encoding="utf-8") as f:
    raw_text = f.read()
enc_text = tokenizer.encode(raw_text)
print(len(enc_text))

enc_sample = enc_text[50:]
context_size = 4 
x=enc_sample[:context_size]
y=enc_sample[1:context_size+1]
print(f"x: {x}")
print(f"y: {y}")