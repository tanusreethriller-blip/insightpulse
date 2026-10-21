from __future__ import annotations

import csv
import io
import random
from datetime import date, timedelta

DEMO_DOC = """SYNTHETIC DEMO POLICY - NOT A REAL PRODUCT POLICY

DemoDesk subscriptions renew monthly on the subscription anniversary date. The workspace billing page shows the renewal date and the current plan.

To cancel a DemoDesk subscription, open Workspace Settings, select Billing, and choose Cancel subscription. Cancellation stops the next renewal. Access continues until the end of the current paid period.

For a suspected duplicate subscription charge, contact DemoDesk support with the invoice reference. Support reviews the payment records before deciding whether a refund is appropriate. This sample policy does not promise an automatic refund.

DemoDesk CSV exports are available from the Reports page. Export permissions depend on the workspace role. Contact a workspace administrator when export access is missing.
"""


def rows(today: date | None = None) -> list[dict]:
    end = today or date.today()
    randomizer = random.Random(17)
    templates = [
        ("The application crashes whenever I upload a large attachment", "reliability", 1),
        ("My dashboard freezes and shows a blank screen after saving", "reliability", 2),
        ("I cannot log in because the verification code never arrives", "access", 1),
        ("The password reset process is confusing and keeps failing", "access", 2),
        ("I was charged twice and cannot find a clear billing explanation", "billing", 1),
        ("Cancelling my subscription was difficult and the renewal date was unclear", "billing", 2),
        ("The reports page is very slow when loading our larger workspace", "performance", 2),
        ("The CSV export does not include our most recent records", "integrations", 2),
        ("I find the navigation confusing and the important button is hidden", "usability", 3),
        ("Customer support resolved my ticket with a helpful response", "support", 5),
        ("The new interface makes our daily reporting easier to understand", "usability", 4),
        ("I love this product and it has become useful every day", "praise", 5),
        ("The workspace is excellent and our whole team enjoys using it", "praise", 5),
        ("Our integration now syncs correctly and the export is reliable", "integrations", 4),
    ]
    result = []
    for index in range(168):
        age = index // 2
        options = templates[:8] + templates if age < 28 else templates
        text, _, rating = randomizer.choice(options)
        result.append({"review_id": f"demo-{index + 1:03}", "date": (end - timedelta(days=age)).isoformat(), "rating": rating, "text": f"{text}. Synthetic observation {index + 1}, workspace {chr(65 + index % 26)}."})
    return result


def csv_bytes(today: date | None = None) -> bytes:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["review_id", "date", "rating", "text"])
    writer.writeheader()
    writer.writerows(rows(today))
    return output.getvalue().encode()
