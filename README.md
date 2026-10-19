# Dual_EC_DRBG Backdoor — Hands-On Demo

A tiny, dependency-free Python demo (standard library only, Python 3.8+) that
shows **how a random number generator can be backdoored by its constants alone**,
using the real NIST **P-256** curve and the real Dual_EC structure.

```
python3 dual_ec_demo.py              # fast version, < 1 second
python3 dual_ec_demo.py --bits 16    # real Dual_EC truncation, ~1-2 minutes
python3 dual_ec_demo.py --seed 7     # reproducible run
```

> Educational only. Dual_EC_DRBG was withdrawn by NIST in 2014 and must not be used.

[Ref: cryptographic-flaws-exploited](https://mikail-eliyah.medium.com/cryptographic-flaws-exploited-39c03c7c5a1a)

The Backdoor: Look at the red arrows. The attacker intercepts r_i, multiplies it by the secret d, and arrives at the State update function, recovering the next state internally.

---

## 1. The generator in 30 seconds

Two public points on the curve, **P** and **Q**. A secret internal state **s**.

```
state update :   s_i = x( s_{i-1} · P )      # multiply a point by a scalar, keep the x-coordinate
output point :   t_i = x( s_i · Q )
output bits  :   out_i = t_i with its top 16 bits thrown away   (256 -> 240 bits)
```

Here `x(...)` means "x-coordinate of the resulting point" (written `φ` or `( )_x` in
your diagrams). Multiplying a point by a scalar is easy; undoing it (finding `s`
from `s·P`) is the hard elliptic-curve discrete log problem. So the state looks safe.

Mapping to your figures:

| Figure notation            | This code                              |
|----------------------------|----------------------------------------|
| `s₀ → s₁` via `(s₀P)ₓ`     | `self.s = x(self.s * P)`               |
| `(s₁Q)ₓ`, truncated → `r₁` | `t & ((1 << (256-drop)) - 1)`          |
| `d·r₁ = d·s₁·Q = s₁·P`     | `scalar_mult(d, candidate_point)`      |

## 2. The backdoor: one relationship

The designer picks a secret number **d** and publishes

```
P = d · Q
```

Nobody can tell P and Q are related just by looking (and nobody explained where
the standard's P and Q came from — the "procedural ignorance" flaw in your notes).

### Attack, step by step (what `recover_state_candidates` does)

1. **See one output** `out_1` (240 bits). Its top 16 bits are missing.
2. **Guess the missing bits.** Try all 2¹⁶ = 65,536 values to rebuild a candidate
   x-coordinate. (This is why throwing away *more* bits would have killed the attack.)
3. **Solve for y.** Plug x into `y² = x³ + ax + b (mod p)`. About half of the x
   values are not on the curve → discarded. The rest give a point `A = ±(s_1·Q)`.
4. **Apply the backdoor.**
   ```
   d · A = d · (s_1 · Q) = s_1 · (d · Q) = s_1 · P
   ```
5. **Read off the state.** `x(s_1·P)` is exactly `s_2`, the *next internal state*.
6. **Filter the ~32,000 candidates.** Only one, when used as the generator's state,
   reproduces the *next* observed output. That's the real state.
7. **Predict forever.** From `s_2` the attacker runs the generator and knows every
   future output.

```
 victim:   s0 ──P──► s1 ──P──► s2 ──P──► s3 ...
                     │Q        │Q        │Q
                    out1      out2      out3

 attacker:  out1 ──(guess bits, solve y)──► ±s1·Q ──(× d)──► s1·P = s2
            then run the generator from s2 -> out3, out4, ...
```

## 3. What you'll see when you run it

```
[SETUP]    Designer picks secret d and publishes P = d*Q
[VICTIM]   Generator emits 4 blocks of "random" bits
[ATTACKER] Brute-forces the dropped bits ... ~133 candidates (8-bit mode)
           Exactly 1 candidate predicts output 2 correctly -> state recovered!
           output 3: predicted == real?  True
           output 4: predicted == real?  True
[CONTROL]  Same attack with a wrong d: prediction succeeds? False
```

The **control** proves the point: the weakness is not the curve and not the
search — it is the secret `d`. Without it the same steps produce nothing useful.

## 4. Things to try

| Experiment | Command / edit | What you learn |
|---|---|---|
| Drop fewer bits | `--bits 4` | Fewer candidates, instant, more candidates are noise-free |
| Real truncation | `--bits 16` | Attack is still totally practical (~1–2 min in pure Python; milliseconds-seconds in optimized code) |
| Drop more bits | `--bits 20` | Search grows 2× per bit — see why truncating much more would have blunted the attack (and why it wasn't done) |
| Different victim | `--seed 1`, `--seed 2` | Works for any secret seed |
| Honest parameters | In `main()`, set `Q = scalar_mult(rng.randrange(2, n), G)` and `P = scalar_mult(rng.randrange(2, n), G)` independently, and give the attacker a guess for d | With unrelated P, Q there is no `d`; the attack has nothing to use |

## 5. Why the real attack was even easier than this demo

- Real Dual_EC needed only ~**30 bytes** of output (one block) to recover the state; the demo uses a second block just to pick among candidates, as a real attacker would.
- TLS often sends nonces/handshake randoms in the clear, giving the attacker that output for free.
- The 2006 variant in your Figure 2 adds `additional_input`, but when it is empty/guessable the attack still goes through.

## 6. Lessons (from your notes)

1. **Appeal to authority** — a NIST standard (and its inclusion in BSAFE, OpenSSL-FIPS, MS CryptoAPI) was trusted despite early warnings (Shumow & Ferguson, 2007).
2. **Procedural ignorance** — unexplained "magic constants" (P, Q) are a red flag. Prefer constants derived transparently ("nothing-up-my-sleeve").
3. **Ignoring early warnings** has real costs; the industry later moved toward Curve25519.

## 7. Files

- `dual_ec_demo.py` — everything: P-256 math, the generator, the attack, the control.
- `README.md` — this tutorial.

## 8. Implementation notes

- Curve arithmetic is hand-written for clarity (Jacobian coordinates for speed). It is **not** constant-time and not for production.
- P-256 has `p ≡ 3 (mod 4)`, so square roots are one `pow(rhs, (p+1)//4, p)`.
- For `--bits ≥ 12` the search uses multiple CPU cores if available.
