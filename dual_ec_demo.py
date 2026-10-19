#!/usr/bin/env python3
"""
Dual_EC_DRBG backdoor demo (educational).

Uses the real NIST P-256 curve and the real Dual_EC structure:

    s_i = x(s_{i-1} * P)          state update
    t_i = x(s_i * Q)              output point (x-coordinate)
    out_i = t_i with its top 16 bits thrown away (240 bits kept)

Backdoor: whoever knows d with P = d*Q can, from ONE output block,
recover the internal state and predict every future output.

Usage:
    python3 dual_ec_demo.py              # fast: drop 8 bits  (256 guesses, <1 s)
    python3 dual_ec_demo.py --bits 16    # real Dual_EC: drop 16 bits (65,536 guesses, ~1-2 min)
    python3 dual_ec_demo.py --seed 7     # reproducible run
"""
import argparse
import random
import time

# ----------------------------------------------------------------------
# 1. NIST P-256 elliptic curve:  y^2 = x^3 + a*x + b  (mod p)
# ----------------------------------------------------------------------
p  = 0xffffffff00000001000000000000000000000000ffffffffffffffffffffffff
a  = p - 3
b  = 0x5ac635d8aa3a93e7b3ebbd55769886bc651d06b0cc53b0f63bce3c3e27d2604b
Gx = 0x6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296
Gy = 0x4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5
n  = 0xffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551
G  = (Gx, Gy)

assert (Gy * Gy - (Gx**3 + a * Gx + b)) % p == 0, "G is not on the curve"


def point_add(P1, P2):
    """Add two points (None = point at infinity)."""
    if P1 is None: return P2
    if P2 is None: return P1
    x1, y1 = P1
    x2, y2 = P2
    if x1 == x2 and (y1 + y2) % p == 0:
        return None
    if P1 == P2:
        m = (3 * x1 * x1 + a) * pow(2 * y1, -1, p) % p
    else:
        m = (y2 - y1) * pow(x2 - x1, -1, p) % p
    x3 = (m * m - x1 - x2) % p
    y3 = (m * (x1 - x3) - y1) % p
    return (x3, y3)


def _jac_double(X, Y, Z):
    if Z == 0 or Y == 0:
        return (1, 1, 0)
    YY = Y * Y % p
    S = 4 * X * YY % p
    M = 3 * (X - Z * Z) * (X + Z * Z) % p          # uses a = -3
    X3 = (M * M - 2 * S) % p
    Y3 = (M * (S - X3) - 8 * YY * YY) % p
    Z3 = 2 * Y * Z % p
    return (X3, Y3, Z3)


def _jac_add_affine(X1, Y1, Z1, x2, y2):
    if Z1 == 0:
        return (x2, y2, 1)
    Z1Z1 = Z1 * Z1 % p
    U2 = x2 * Z1Z1 % p
    S2 = y2 * Z1 * Z1Z1 % p
    H = (U2 - X1) % p
    R = (S2 - Y1) % p
    if H == 0:
        return _jac_double(X1, Y1, Z1) if R == 0 else (1, 1, 0)
    HH = H * H % p
    HHH = H * HH % p
    V = X1 * HH % p
    X3 = (R * R - HHH - 2 * V) % p
    Y3 = (R * (V - X3) - Y1 * HHH) % p
    Z3 = Z1 * H % p
    return (X3, Y3, Z3)


def scalar_mult(k, P1):
    """k * P1 (double-and-add in Jacobian coordinates: fast, one inversion)."""
    if P1 is None or k == 0:
        return None
    x, y = P1
    X, Y, Z = 1, 1, 0
    for bit in bin(k)[2:]:
        X, Y, Z = _jac_double(X, Y, Z)
        if bit == "1":
            X, Y, Z = _jac_add_affine(X, Y, Z, x, y)
    if Z == 0:
        return None
    zi = pow(Z, -1, p)
    return (X * zi * zi % p, Y * zi * zi * zi % p)


def y_from_x(x):
    """Solve the curve equation for y. Returns None if x is not on the curve.
    (p = 3 mod 4, so a square root is just rhs^((p+1)/4).)"""
    rhs = (x * x * x + a * x + b) % p
    y = pow(rhs, (p + 1) // 4, p)
    return y if (y * y) % p == rhs else None


# ----------------------------------------------------------------------
# 2. The Dual_EC generator
# ----------------------------------------------------------------------
class DualEC:
    def __init__(self, P, Q, seed, drop_bits):
        self.P, self.Q, self.s = P, Q, seed
        self.drop = drop_bits

    def next_block(self):
        self.s = scalar_mult(self.s, self.P)[0]          # s_i = x(s_{i-1} * P)
        t = scalar_mult(self.s, self.Q)[0]               # t_i = x(s_i * Q)
        return t & ((1 << (256 - self.drop)) - 1)        # drop the TOP bits


def output_from_state(s, Q, drop):
    """What a generator in state s would output next (used by the attacker)."""
    t = scalar_mult(s, Q)[0]
    return t & ((1 << (256 - drop)) - 1)


# ----------------------------------------------------------------------
# 3. The attack
# ----------------------------------------------------------------------
def _try_top(args):
    top, out1, d, drop = args
    x = (top << (256 - drop)) | out1
    if x >= p:
        return None
    y = y_from_x(x)
    if y is None:                           # not a point on the curve -> discard
        return None
    return scalar_mult(d, (x, y))[0]        # THE BACKDOOR STEP: d * A = s_i * P


def recover_state_candidates(out1, d, drop):
    """From one output block, return every possible NEXT internal state.

    1. Guess the dropped top bits           -> candidate x-coordinates
    2. Solve the curve equation for y       -> candidate points A = +-(s_i * Q)
    3. Multiply by the secret d             -> d*A = s_i*(d*Q) = s_i*P
    4. x(s_i * P) is exactly s_{i+1}, the NEXT internal state
    (-A gives the same x, so the sign of y does not matter.)
    """
    jobs = [(top, out1, d, drop) for top in range(1 << drop)]
    if drop >= 12:                          # big search: use all CPU cores
        from multiprocessing import Pool
        with Pool() as pool:
            res = pool.map(_try_top, jobs, chunksize=256)
    else:
        res = [_try_top(j) for j in jobs]
    return [r for r in res if r is not None]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bits", type=int, default=8,
                    help="top bits dropped from each output (real Dual_EC = 16)")
    ap.add_argument("--seed", type=int, default=None, help="RNG seed for repeatability")
    args = ap.parse_args()
    rng = random.Random(args.seed)
    drop = args.bits

    print("=" * 64)
    print(" Dual_EC_DRBG backdoor demo   (curve: NIST P-256)")
    print("=" * 64)

    # --- Setup: the designer picks Q and a secret d, publishes P = d*Q
    Q = G
    d = rng.randrange(2, n)
    P = scalar_mult(d, Q)
    print("\n[SETUP] Designer picks secret d and publishes P = d*Q")
    print(f"  Q = G (public)\n  P = {hex(P[0])[:26]}... (public, looks random)")
    print("  d = <kept secret>")

    # --- Victim runs the generator
    victim = DualEC(P, Q, rng.randrange(2, n), drop)
    print(f"\n[VICTIM] Generator drops top {drop} bits of each output "
          f"(keeps {256 - drop}).")
    blocks = [victim.next_block() for _ in range(4)]
    for i, blk in enumerate(blocks, 1):
        print(f"  output {i}: {blk:0{(256 - drop + 3) // 4}x}"[:60] + "...")

    # --- Attacker sees only block 1 (+ block 2 to pick the right candidate)
    print(f"\n[ATTACKER] Sees output 1 and knows d. Brute-forcing {1 << drop} "
          f"possible top-bit values...")
    t0 = time.time()
    cands = recover_state_candidates(blocks[0], d, drop)
    print(f"  {len(cands)} candidate internal states "
          f"(others were not on the curve)  [{time.time() - t0:.1f}s]")

    # Filter: the right state must predict output 2 correctly
    winner = None
    for s_next in cands:
        if output_from_state(s_next, Q, drop) == blocks[1]:
            winner = s_next
            break
    if winner is None:
        print("  Attack failed (should not happen with the real d).")
        return
    print(f"  Exactly 1 candidate predicts output 2 correctly -> state recovered!")

    # --- Predict the future
    print("\n[ATTACKER] Cloning the generator from the recovered state.")
    print("  output 2: predicted == real?  True   (that is how we picked the winner)")
    print("  Now predicting outputs 3 and 4 with NO further access to the victim:")
    atk = DualEC(P, Q, 0, drop)
    atk.s = winner            # internal state s_2 (the state that produced output 2)
    ok = True
    for i in (3, 4):
        pred = atk.next_block()
        match = pred == blocks[i - 1]
        ok &= match
        print(f"  output {i}: predicted == real?  {match}")
    print("\n  >>> BACKDOOR WORKS: all future 'random' output is known. <<<"
          if ok else "\n  Something went wrong.")

    # --- Control: attacker WITHOUT d
    if drop >= 12:
        print("\n[CONTROL] skipped for --bits >= 12 (would repeat the long search). "
              "Run without --bits to see it.")
        return
    print("\n[CONTROL] Same attack with a wrong guess for d:")
    bad = recover_state_candidates(blocks[0], rng.randrange(2, n), drop)
    hit = any(output_from_state(s, Q, drop) == blocks[1] for s in bad)
    print(f"  Prediction succeeds? {hit}   (the secret d is what makes it work)")


"""
    # --- Control: attacker WITHOUT d
    if drop >= 12:
        print("\n[CONTROL] skipped for --bits >= 12 (would repeat the long search). "
              "Run with a smaller --bits to see it.")
        return

    print(f"\n[CONTROL] Same attack with {args.trials} random wrong values for d:")
    hits = 0
    for k in range(1, args.trials + 1):
        wrong_d = rng.randrange(2, n)
        if wrong_d == d:                       # astronomically unlikely, but be safe
            continue
        bad = recover_state_candidates(blocks[0], wrong_d, drop)
        hit = any(output_from_state(s, Q, drop) == blocks[1] for s in bad)
        hits += hit
        print(f"  wrong d #{k}: {len(bad)} candidates, prediction succeeds? {hit}")

    print(f"\n  Result: {hits}/{args.trials} wrong values of d worked "
          f"(the real d: 1/1)")
"""
"""
Test 1: result=1, expected=1
Test 2: result=0, expected=0
Test 3: result=4, expected=4
Test 4: result=4, expected=4
Test 5: result=9, expected=9
Test 6: result=4, expected=4
Test 7: result=0, expected=0
All tests passed.
"""

import sys

if __name__ == "__main__":
    #sys.argv = ["dual_ec_demo.py", "--bits", "8", "--seed", "7"] # 16
    #main()

    ap = argparse.ArgumentParser()
    ap.add_argument("--bits", type=int, default=8,
                    help="top bits dropped from each output (real Dual_EC = 16)")
    ap.add_argument("--seed", type=int, default=None, help="RNG seed for repeatability")
    ap.add_argument("--trials", type=int, default=5,
                    help="number of random wrong d values to test in the control")
    args, _ = ap.parse_known_args()      # was: ap.parse_args()
    rng = random.Random(args.seed)
    drop = args.bits

"""
ap.add_argument("--trials", type=int, default=5,
                help="number of random wrong d values to test in the control")
args, _ = ap.parse_known_args()
"""


"""
================================================================
 Dual_EC_DRBG backdoor demo   (curve: NIST P-256)
================================================================

[SETUP] Designer picks secret d and publishes P = d*Q
  Q = G (public)
  P = 0xb146da258d0458dee94acbd9... (public, looks random)
  d = <kept secret>

[VICTIM] Generator drops top 8 bits of each output (keeps 248).
  output 1: e1cbe651ceb42967299a9549a73ea084a6f989825d8f8ff5...
  output 2: 5121a899d03409d025853972e25f68b2fd433a89ec578574...
  output 3: 961403444d5fa0ea903ec18b0b1023341260fc1546ebcfb8...
  output 4: 15a5961eda9f4ff5b11afa2311b43931ca03f7357cd3e3d4...

[ATTACKER] Sees output 1 and knows d. Brute-forcing 256 possible top-bit values...
  141 candidate internal states (others were not on the curve)  [0.9s]
  Exactly 1 candidate predicts output 2 correctly -> state recovered!

[ATTACKER] Cloning the generator from the recovered state.
  output 2: predicted == real?  True   (that is how we picked the winner)
  Now predicting outputs 3 and 4 with NO further access to the victim:
  output 3: predicted == real?  True
  output 4: predicted == real?  True

  >>> BACKDOOR WORKS: all future 'random' output is known. <<<

[CONTROL] Same attack with a wrong guess for d:
  Prediction succeeds? False   (the secret d is what makes it work)

What each stage shows:
- 256 guesses → 141 candidates. Dropping 8 bits means the attacker doesn’t know the top 8 bits of the point’s x-coordinate, so there are 256 possibilities. Roughly half of random x values have no matching y on the curve (the right-hand side of the curve equation isn’t a quadratic residue), so about 128 survive. Getting 141 is in line with that.
- Exactly 1 winner. Each surviving candidate gives a guess at the next internal state. Only the true one reproduces output 2, so a single extra block disambiguates. With the real 16-bit drop, you’d have ~32,000 candidates instead of 141, and one more block still pins it down.
- Outputs 3 and 4 predicted correctly. Once the attacker holds the internal state, the generator is deterministic, so every future output is known. That’s the practical consequence of the backdoor: one observed output block exposes the entire future stream.
- Control fails. With a wrong d, the multiplication d*A no longer equals s·P, so none of the candidates predict output 2. The relationship P = d·Q is the whole backdoor.

Why it matters historically: NIST SP 800-90A originally specified Dual_EC_DRBG with fixed constants P and Q and no explanation of how Q was chosen. In 2007, Shumow and Ferguson showed that whoever generated those constants could know a d linking them. Later reporting, including the Snowden documents, indicated the NSA likely did exactly that, and NIST withdrew the algorithm in 2014. The real attack has the same structure as your demo, just on a different curve and with the 16-bit search.

"""
