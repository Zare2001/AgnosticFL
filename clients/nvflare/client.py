"""NVFlare hospital client: the standard Client API loop around flhub_core.train.

NVFlare deploys this script to each hospital as the job's custom code (ScriptRunner, in-process).
Hospital-side configuration is only the data file: $FLHUB_DATA_PATH, else flhub_core's default
<$FLHUB_DATA_DIR or ~/flhub-data>/<NVFlare site name>.csv.
"""

import os

import nvflare.client as flare
from nvflare.client import FLModel, ParamsType

import flhub_core
from flhub_core.data import load_site, site_path
from flhub_core.train import TrainConfig, train


def main():
    flare.init()
    site = flare.get_site_name()
    path = os.environ.get("FLHUB_DATA_PATH") or str(site_path(site))
    data = load_site(path)
    print(f"[flhub] {site}: {len(data)} examples from {path}; flhub_core {flhub_core.code_version()}", flush=True)

    while flare.is_running():
        global_model = flare.receive()
        if global_model is None:  # job finished
            break
        cfg = dict(global_model.meta.get("train_config", {}))
        cfg["round"] = global_model.current_round
        weights, num_examples, metrics = train(global_model.params, data, TrainConfig.from_dict(cfg))
        print(f"[flhub] {site}: round {cfg['round']} n={num_examples} {metrics}", flush=True)
        flare.send(
            FLModel(
                params_type=ParamsType.FULL,
                params=weights,
                metrics=metrics,
                meta={
                    "num_examples": num_examples,
                    "NUM_STEPS_CURRENT_ROUND": num_examples,  # NVFlare's standard aggregation weight key
                    "client_code_version": flhub_core.code_version(),
                },
            )
        )


if __name__ == "__main__":
    main()
