"""Prints the daily report's cell values and the 9:00 summary text for a fixed website-only day.

Run it once in a checkout of `main` and once on this branch; the two outputs must be identical
(zalo-intake spec R7 "Website-only days", criterion 11):

    git archive main | tar -x -C /tmp/main-tree
    (cd /tmp/main-tree && python /path/to/this/script.py) > main.json
    python scripts/compare_report_with_main.py > branch.json && cmp main.json branch.json
"""
import json
import os
import sys

sys.path.insert(0, os.getcwd())

from hasc.zalo.summary import summary_text  # noqa: E402
from tests import test_daily_report as daily  # noqa: E402


class WebsiteDay(daily.DailyReport):
    def runTest(self):
        self.seed()
        sheet, photo = self.sheet("T3", 2, self.an, page_no=3)
        self.roll(sheet, photo, 7, "Delta", "PE", 30, 700, 1000, machine="T 6")
        self.roll(sheet, photo, 9, "Delta", "PE", 30, 700, 1000, machine="XX")
        cells = [[cell.value for cell in row] for row in self.build().iter_rows()]
        print(json.dumps({"cells": cells, "summary": summary_text(self.conn, daily.DAY)}, ensure_ascii=False))


if __name__ == "__main__":
    test = WebsiteDay()
    test.setUp()
    test.runTest()
    test.doCleanups()
