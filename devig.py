"""Turn a bookmaker's implied probabilities (summing to more than one) into fair probabilities.
Three methods: multiplicative normalisation (the naive one), the power method of Clarke, Kovalchik & Ingram (2017), and
Shin's (1991, 1992) insider-trading model, which Strumbelj (2014) found the most accurate forecast on bookmaker odds.
Shin's z is the implied share of insider money; both Shin and the power method shrink longshots more than favourites."""
import math


def multiplicative(ps):
    s = sum(ps)
    return [p / s for p in ps]


def power(ps):
    """Find k such that sum(p_i^k) = 1 and return p_i^k."""
    lo, hi = 1.0, 20.0
    for _ in range(100):
        k = (lo + hi) / 2
        s = sum(p ** k for p in ps)
        if s > 1: lo = k
        else: hi = k
    return [p ** k for p in ps]


def shin(ps, return_z=False):
    """Shin's model: implied price pi_i = z + (1 - z) q_i ... in the normalised form, fair q_i = (sqrt(z^2 + 4(1-z) pi_i^2 / B) - z) / (2(1-z)),
    with B = sum(pi) and z chosen so that the q_i sum to one."""
    B = sum(ps)
    def q(z): return [(math.sqrt(z * z + 4 * (1 - z) * p * p / B) - z) / (2 * (1 - z)) for p in ps]
    lo, hi = 0.0, min(0.9, max(B - 1, 1e-9))
    for _ in range(100):
        z = (lo + hi) / 2
        if sum(q(z)) > 1: lo = z
        else: hi = z
    z = (lo + hi) / 2
    return (q(z), z) if return_z else q(z)


if __name__ == "__main__":
    for ps in ([0.55, 0.50], [0.90, 0.15], [0.05, 0.98], [0.30, 0.30, 0.30, 0.20]):
        print(ps, "mult", [round(x, 3) for x in multiplicative(ps)], "power", [round(x, 3) for x in power(ps)], "shin", [round(x, 3) for x in shin(ps)], "z=%.3f" % shin(ps, True)[1])
