# Buy or Wait? code

The current implementation includes strict participant-data normalization and the
25-sample evaluation harness. It does not yet forecast cash flow or recommend a
payment plan.

From the repository root, validate and bundle every participant-facing row:

```text
python code/main.py --dataset-dir dataset --check-data
```

Python integration example:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path("code").resolve()))
from buy_or_wait.data import load_dataset

repository = load_dataset(Path("dataset"))
bundle = repository.bundle_for("request_26")
```

`bundle` contains the typed request, profile, all user events, payment options,
source messages, source images, and the exact dated exchange-rate records required
by the user's foreign-currency cash events. Sample requests and their completed
labels are stored separately; production `Request` objects never contain labels.

See `evaluation/README.md` for sample-scoring commands.
