"""Generate a new 24-word seed phrase. Run OFFLINE, write it on paper, never paste it anywhere online.
   pip install mnemonic      then:  python tools/gen_seed.py
"""
from mnemonic import Mnemonic
print(Mnemonic("english").generate(strength=256))
