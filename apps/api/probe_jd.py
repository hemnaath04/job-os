import sys

sys.path.insert(0, "src")
from job_os.services.tailor import _jd_requirements  # noqa: E402

jd = {
    "required_skills": [
        "Experience with C/C++/Java/Go/Python",
        "data structures",
        "algorithms",
        "problem-solving",
        "optimization techniques",
        "machine learning systems",
        "concurrent systems",
        "profiling tools",
        "architectures",
    ],
    "preferred_skills": ["computer graphics", "computer vision", "deep learning"],
    "technologies": ["AI Platform", "optimization"],
}
reqs, prose, excl = _jd_requirements(jd)
print("--- requirements ---")
for r in reqs:
    print("PREF" if r.preferred else "MUST", repr(r.label), "->", r.alternatives)
print("prose:", prose)
print("excluded:", excl)
print("must count:", sum(1 for r in reqs if not r.preferred))
