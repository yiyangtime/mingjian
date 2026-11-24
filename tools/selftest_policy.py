"""
政策解析回归自测：4 类样本，断言字段命中与失败判定。

跑法：python tools/selftest_policy.py
改了 policy.py 的规则后必须跑一遍，避免"改好一处、坏掉另一处"。
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mingjian.services import policy  # noqa: E402

CASES = [
    {
        "name": "标准省级申报通知",
        "text": """江苏省工业和信息化厅
苏工信装备〔2026〕112号
关于2026年度江苏省首台（套）重大装备认定工作的通知
各设区市工业和信息化局：
二、支持标准。对通过认定的首台（套）装备，按销售价格的15%给予奖励，单台最高奖励500万元。
三、申报主体。在江苏省境内注册、具有独立法人资格的装备制造企业。
四、截止时间。2026年10月25日前完成网上申报。
2026年8月20日""",
        "status": "ok",
        "has": ["issuer", "docNo", "date", "region", "support", "deadline"],
        "deadline": "2026-10-25",
    },
    {
        "name": "区间值（应降置信度转复核）",
        "text": """广西壮族自治区工业和信息化厅
桂工信装备〔2026〕94号
关于振兴机械装备产业的若干政策措施
各市工业和信息化局：
二、支持标准。对技术改造项目按投资额的10%—15%给予补助，具体比例由各市确定。
四、申报时间。2026年12月5日前报送。
2026年9月20日""",
        "status": "partial",
        "low": ["support"],
    },
    {
        "name": "缺截止日期（应留空不猜）",
        "text": """四川省经济和信息化厅
川经信装备〔2026〕134号
关于组织申报2026年装备制造数字化改造项目的通知
各市（州）经济和信息化局：
二、支持标准。按项目设备投资额的18%给予补助，单个项目最高1500万元。
三、申报主体。在四川省内注册的装备制造企业。
2026年8月30日""",
        "status": "partial",
        "deadline_empty": True,
    },
    {
        "name": "非公告文本（应判失败）",
        "text": "这是一份残缺的材料，只有一句话，用来验证失败分支。" * 2,
        "status": "failed",
        "conf": 0,
    },
]

fails = 0
for i, c in enumerate(CASES, 1):
    d = policy.parse_text(f"sample{i}.txt", c["text"], doc_id=f"S{i}")
    ok = d["status"] == c["status"]
    ok = ok and all(d["f"].get(k) for k in c.get("has", []))
    if c.get("deadline"):
        ok = ok and d["f"].get("deadline") == c["deadline"]
    if c.get("deadline_empty"):
        ok = ok and not d["f"].get("deadline")
    if c.get("low"):
        ok = ok and all(k in (d.get("lowFields") or []) for k in c["low"])
    if "conf" in c:
        ok = ok and d["conf"] == c["conf"]
    print(("PASS" if ok else "FAIL"), c["name"], "->", d["status"], d["conf"],
          {k: v for k, v in d["f"].items() if v})
    fails += 0 if ok else 1

print()
print(f"{len(CASES) - fails}/{len(CASES)} 通过")
sys.exit(1 if fails else 0)
