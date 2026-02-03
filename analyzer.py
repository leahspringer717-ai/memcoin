
def anti_rug_score(liquidity, volume, change):
    score = 0
    if liquidity < 50000:
        score += 30
    if volume < 10000:
        score += 30
    if abs(change) > 50:
        score += 40
    return min(score, 100)

def is_pump(change, volume):
    return change > 10 and volume > 50000
