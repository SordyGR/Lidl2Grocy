# Lidl Parser/tests/test_receipt_discount.py
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from receipt_parser import parse_receipt


PROMO_KEPT = (
    "7 \u039d\u03bf\u03b5 2026\n"
    "\u039f\u0394\u039f\u039d\u03a4\u039f\u039a\u03a1\u0395\u039c\u0391 \u03a6\u0398.75ML           1,35  24.00%\n"
    "     \u039c\u03b5 \u03b1\u03b3\u03bf\u03c1\u03ac 2 \u03a4\u0395\u039c -1\u20ac (\u03a3\u0395\u03a4 \u0391     -0,50\n"
    "\u0393\u0391\u039b\u0391 1L                       1,74  13.00%\n"
    "    2 x          0,87      \n"
    "\u03a3\u03a5\u039d\u039f\u039b\u039f                               2,59\n"
)


def test_promo_text_minus_does_not_shadow_discount_amount():
    """A promo fragment like '-1€' must not be read as the discount amount.

    Regression: receipt 26-11-07 booked -1.00 instead of -0.50 on each of
    two 'Με αγορά 2 ΤΕΜ -1€ ... -0,50' lines, so the import failed the
    total check with parsed=91.46 vs receipt 92.45.
    """
    df = parse_receipt(PROMO_KEPT, "26-11-07.txt")
    row = df[df["Product"] == "\u039f\u0394\u039f\u039d\u03a4\u039f\u039a\u03a1\u0395\u039c\u0391 \u03a6\u0398.75ML"].iloc[0]
    assert abs(row["Discount"] - 0.50) < 0.001
    parsed = round(float((df["Qty"] * df["Unit Price"] - df["Discount"]).sum()), 2)
    assert abs(parsed - 2.59) < 0.005


def test_regular_discount_line_still_parses():
    text = (
        "7 \u039d\u03bf\u03b5 2026\n"
        "\u03a4\u039f\u03a1\u03a4\u0399 200G                    2,78  13.00%\n"
        "    2 x          1,39      \n"
        "     \u03ad\u03ba\u03c0\u03c4\u03c9\u03c3\u03b7 Lidl Plus             -0,10\n"
        "     \u03ad\u03ba\u03c0\u03c4\u03c9\u03c3\u03b7                       -0,06\n"
    )
    df = parse_receipt(text, "26-09-22.txt")
    assert abs(df.iloc[0]["Discount"] - 0.16) < 0.001
