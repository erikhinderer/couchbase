# Embedding Service

Loads any `sentence-transformers`-compatible Hugging Face model on first request
and serves `/embed` for inference. The 10 models offered in the UI are defined in
`backend/app/core/embedding_models.py`; this service doesn't hard-code that list,
so you can experiment with other sentence-transformers model ids by calling
`/warmup` or `/embed` directly.

- `GET /health` -- readiness + list of models currently loaded in memory
- `POST /warmup {"model_id": "..."}` -- pre-loads a model (called automatically
  during job validation so the first real document doesn't pay cold-load latency)
- `POST /embed {"model_id": "...", "texts": ["..."], "text_prefix": ""}` -- returns
  L2-normalized embedding vectors

Models are cached on disk under `HF_HOME` (mounted as the `embedding_model_cache`
volume in docker-compose.yml) and in memory for the life of the container -- the
first document embedded with a given model will be slower (downloading + loading
the checkpoint); everything after that is fast.
