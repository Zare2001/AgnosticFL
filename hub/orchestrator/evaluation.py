"""Global evaluation on the central test (or validation) split (stage-2 PLAN.md §4.3).

Metrics: loss, accuracy, balanced accuracy (mean per-class recall), macro one-vs-rest AUC and the
site-shaped accuracy: the per-class test accuracies reweighted to each site's training label
histogram (min / mean / std over the sites and per site), with no protocol change.
Hub-only code: the forward pass uses flhub_core's model zoo, the metrics live here.
"""

from __future__ import annotations

import numpy as np
import torch
import torch.nn.functional as F
from flhub_core.data import load_test, load_val
from flhub_core.model import build_model, set_weights
from flhub_core.train import binary_auc


class Evaluator:
    def __init__(self, dataset: str, model: str, num_classes: int, split: str = "test",
                 site_hists: dict[str, list[int]] | None = None, threads: int = 1, batch_size: int = 1024) -> None:
        if split not in ("test", "val"):
            raise ValueError("split must be test or val")
        self.data = load_test(dataset=dataset) if split == "test" else load_val(dataset=dataset)
        self.prefix = f"global/{split}_"
        self.model_id, self.num_classes = model, num_classes
        self.site_hists = {s: np.asarray(h, dtype=np.float64) for s, h in (site_hists or {}).items()}
        self.threads, self.batch_size = max(1, int(threads)), batch_size

    @torch.no_grad()
    def logits(self, weights) -> np.ndarray:
        prev = torch.get_num_threads()
        torch.set_num_threads(self.threads)
        try:
            model = build_model(self.model_id, self.num_classes)
            set_weights(model, dict(weights))
            model.eval()
            x = torch.from_numpy(self.data.x)
            out = [model(x[i:i + self.batch_size]).numpy() for i in range(0, len(self.data), self.batch_size)]
        finally:
            torch.set_num_threads(prev)
        return np.concatenate(out).astype(np.float64)

    def __call__(self, weights) -> dict[str, float]:
        z = self.logits(weights)
        y = self.data.y
        z = z - z.max(axis=1, keepdims=True)
        logp = z - np.log(np.exp(z).sum(axis=1, keepdims=True))
        prob = np.exp(logp)
        pred = prob.argmax(axis=1)
        p = self.prefix
        out = {f"{p}loss": float(-logp[np.arange(len(y)), y].mean()), f"{p}acc": float((pred == y).mean())}
        recall = np.array([(pred[y == c] == c).mean() if (y == c).any() else np.nan for c in range(self.num_classes)])
        out[f"{p}bal_acc"] = float(np.nanmean(recall))
        aucs = [binary_auc((y == c).astype(np.int64), prob[:, c]) for c in range(self.num_classes)]
        out[f"{p}auc"] = float(np.nanmean(aucs))
        if self.site_hists:
            shaped = {}
            for site, h in self.site_hists.items():
                w = h / h.sum()
                ok = ~np.isnan(recall)
                shaped[site] = float((w[ok] * recall[ok]).sum() / w[ok].sum())
                out[f"siteshaped/{site}/acc"] = shaped[site]
            vals = np.array(list(shaped.values()))
            out.update({"siteshaped/min_acc": float(vals.min()), "siteshaped/mean_acc": float(vals.mean()),
                        "siteshaped/std_acc": float(vals.std())})
        return out


@torch.no_grad()
def _check_against_torch(weights, ev: Evaluator) -> float:
    """Mean cross-entropy from torch (used in tests to cross-check the numpy loss)."""
    z = torch.from_numpy(ev.logits(weights))
    return float(F.cross_entropy(z, torch.from_numpy(ev.data.y)))
