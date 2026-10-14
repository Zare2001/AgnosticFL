"""Minimal NVFlare hospital client for the hub (example).

This is the code a hospital runs when its NVFlare client receives the hub's relay job: the
standard NVFlare Client API loop around one call to `flhub_core.train`. It implements the client
contract (docs/client-contract.md):

    receive  the global weights (FULL, numpy) and the training configuration
    run      flhub_core.train(weights, the site's data, config)
    return   the new weights (FULL), num_examples, the metrics and flhub_core.code_version()

Comments marked "NVFlare" are NVFlare's standard Client API pattern; comments marked "flhub" are
what the hub needs on top. The production client is clients/nvflare/client.py: the same steps
without these comments. Hospitals do not install this file: NVFlare ships it inside the job
(custom/client.py) and runs it in-process with ScriptRunner (see ../server/relay_job.py).
"""

import os

import nvflare.client as flare  # NVFlare: the Client API
from nvflare.client import FLModel, ParamsType  # NVFlare: the model container exchanged with the server

import flhub_core  # flhub: the shared, deterministic training library (shipped in the job's custom/)
from flhub_core.data import load_site, site_path
from flhub_core.train import TrainConfig, train


def main() -> None:
    flare.init()  # NVFlare: connect this script to the client's job process
    site = flare.get_site_name()  # NVFlare: the client name from the startup kit = the hub's site id

    # flhub: the only hospital setting is the data file: $FLHUB_DATA_PATH, else $FLHUB_DATA_DIR/<site>.csv.
    # load_site() returns a lazy handle; train() picks the data variant named in the config per round.
    path = os.environ.get("FLHUB_DATA_PATH") or str(site_path(site))
    data = load_site(path)
    print(f"[example] {site}: data {path}, flhub_core {flhub_core.code_version()}", flush=True)

    while flare.is_running():  # NVFlare: one iteration per task the relay sends to this site
        model = flare.receive()  # NVFlare: model.params = {tensor name: float32 numpy array}, FULL weights
        if model is None:  # NVFlare: the job has ended
            break

        # flhub: the hub's train_config travels in model.meta; model.current_round is the hub round,
        # which seeds the data order. Pass both on unchanged: the library decides what each key means.
        config = {"round": model.current_round, **model.meta.get("train_config", {})}
        weights, num_examples, metrics = train(model.params, data, TrainConfig.from_dict(config))
        print(f"[example] {site}: round {config['round']}, {num_examples} examples, "
              f"loss {metrics['train_loss']:.4f}", flush=True)

        flare.send(FLModel(  # NVFlare: reply with a standard FLModel
            params_type=ParamsType.FULL,  # NVFlare: the full weights, not a difference
            params=weights,
            metrics=metrics,  # flhub: train_loss, train_acc, timings, peak_rss_mb, data_fp (data check)
            meta={
                "num_examples": num_examples,  # flhub: the FedAvg weight of this site
                "NUM_STEPS_CURRENT_ROUND": num_examples,  # NVFlare: its own aggregation weight key
                "client_code_version": flhub_core.code_version(),  # flhub: proves which training code ran
            },
        ))


if __name__ == "__main__":
    main()
