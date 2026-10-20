"""Conservative literal compound keys; equality is not structural identity."""
from decimal import Decimal, InvalidOperation
import unicodedata


def compound_key(row):
    names = row.get('Ingredient Name')
    formula = row.get('Molecular Formula')
    weight = row.get('Molecular Weight')
    if (not isinstance(names, list) or len(names) != 1 or not isinstance(names[0], str) or
            not names[0].strip() or not isinstance(formula, str) or not formula.strip()):
        return None
    name = unicodedata.normalize('NFKC', names[0]).strip().casefold()
    formula = formula.strip()
    if name in ('unknown', 'n/a', 'none', '-') or formula.casefold() in ('unknown', 'n/a', 'none', '-'):
        return None
    try:
        number = Decimal(str(weight))
        if not number.is_finite() or number <= 0:
            return None
    except InvalidOperation:
        return None
    parts = number.as_tuple()
    if len(parts.digits) > 128 or abs(parts.exponent) > 128:
        return None
    digits = list(parts.digits); exponent = parts.exponent
    while len(digits) > 1 and digits[-1] == 0:
        digits.pop(); exponent += 1
    # Decimal.normalize() uses context precision and can round distinct long values into one key.
    return name, formula, ''.join(map(str, digits))+'e'+str(exponent)
