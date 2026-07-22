"""
Curated registry of the top 10 text-embedding models on Hugging Face, ranked by
cumulative download count on the `sentence-similarity` task listing
(https://huggingface.co/models?pipeline_tag=sentence-similarity&sort=downloads),
checked live in July 2026. Download figures are point-in-time snapshots and will
drift as models continue to be pulled -- they're included for context, not as a
live-updating leaderboard.

Every model here is loadable via the `sentence-transformers` library, which is
what embedding-service/app/main.py uses to actually generate vectors. Dimensions
must match what's declared here -- they're used to size the Couchbase FTS vector
index created by core/validator.py, so if you add a model, verify its real output
dimensionality (check the model card) rather than guessing.

Some model families expect a short instruction prefix prepended to input text for
best retrieval quality (e5's "query: "/"passage: ", nomic's "search_document: ").
`text_prefix` is applied to the "passage" (document-side) case, since this agent
only ever embeds stored JSON documents, never search queries.
"""
from __future__ import annotations

from app.models.schemas import EmbeddingModelInfo

EMBEDDING_MODELS: list[EmbeddingModelInfo] = [
    EmbeddingModelInfo(
        model_id="sentence-transformers/all-MiniLM-L6-v2",
        display_name="all-MiniLM-L6-v2",
        dimensions=384,
        popularity_rank=1,
        approx_downloads="~255M",
        description=(
            "The single most-downloaded sentence-embedding model on Hugging Face. Tiny "
            "(80MB, 6 layers) and very fast on CPU, with solid general-purpose English "
            "semantic-similarity quality. The default choice when in doubt."
        ),
        languages="English",
        similarity_metric="cosine",
        approx_size_mb=90,
    ),
    EmbeddingModelInfo(
        model_id="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        display_name="paraphrase-multilingual-MiniLM-L12-v2",
        dimensions=384,
        popularity_rank=2,
        approx_downloads="~50M",
        description=(
            "Multilingual (50+ languages) variant of the MiniLM family. Good default "
            "when a bucket's JSON documents mix languages."
        ),
        languages="Multilingual (50+ languages)",
        similarity_metric="cosine",
        approx_size_mb=470,
    ),
    EmbeddingModelInfo(
        model_id="BAAI/bge-m3",
        display_name="BGE-M3",
        dimensions=1024,
        popularity_rank=3,
        approx_downloads="~34.6M",
        description=(
            "BAAI's flagship multi-functionality model: dense, sparse, and multi-vector "
            "retrieval in one checkpoint, and the most-downloaded open-weight embedding "
            "model in production RAG deployments. Higher quality than MiniLM at the cost "
            "of larger vectors and slower inference."
        ),
        languages="Multilingual (100+ languages)",
        similarity_metric="dot_product",
        approx_size_mb=2270,
    ),
    EmbeddingModelInfo(
        model_id="sentence-transformers/all-mpnet-base-v2",
        display_name="all-mpnet-base-v2",
        dimensions=768,
        popularity_rank=4,
        approx_downloads="~33.7M",
        description=(
            "The highest-quality model in the original sentence-transformers 'all-*' "
            "series -- a common step up from MiniLM when accuracy matters more than raw "
            "throughput."
        ),
        languages="English",
        similarity_metric="cosine",
        approx_size_mb=420,
    ),
    EmbeddingModelInfo(
        model_id="nomic-ai/nomic-embed-text-v1.5",
        display_name="Nomic Embed Text v1.5",
        dimensions=768,
        popularity_rank=5,
        approx_downloads="~15.4M",
        description=(
            "Matryoshka-trained model: the full 768-dim vector can be truncated to 64-768 "
            "dims with graceful quality degradation, useful if storage/index size is tight. "
            "Requires a task prefix on input text; this agent uses the document-indexing "
            "prefix since it only embeds stored JSON, never search queries."
        ),
        languages="English",
        similarity_metric="cosine",
        text_prefix="search_document: ",
        approx_size_mb=550,
    ),
    EmbeddingModelInfo(
        model_id="intfloat/multilingual-e5-small",
        display_name="Multilingual E5 Small",
        dimensions=384,
        popularity_rank=6,
        approx_downloads="~11.8M",
        description=(
            "Small, fast multilingual member of Microsoft's E5 family. Good choice for "
            "high document volume where the MiniLM multilingual model isn't quite accurate "
            "enough."
        ),
        languages="Multilingual (100+ languages)",
        similarity_metric="cosine",
        text_prefix="passage: ",
        approx_size_mb=470,
    ),
    EmbeddingModelInfo(
        model_id="sentence-transformers/paraphrase-multilingual-mpnet-base-v2",
        display_name="paraphrase-multilingual-mpnet-base-v2",
        dimensions=768,
        popularity_rank=7,
        approx_downloads="~10.1M",
        description="Multilingual MPNet variant -- higher quality than the MiniLM multilingual model, larger and slower.",
        languages="Multilingual (50+ languages)",
        similarity_metric="cosine",
        approx_size_mb=970,
    ),
    EmbeddingModelInfo(
        model_id="intfloat/multilingual-e5-base",
        display_name="Multilingual E5 Base",
        dimensions=768,
        popularity_rank=8,
        approx_downloads="~7.1M",
        description="Base-size E5 multilingual model; a middle ground between e5-small and larger E5/BGE checkpoints.",
        languages="Multilingual (100+ languages)",
        similarity_metric="cosine",
        text_prefix="passage: ",
        approx_size_mb=1110,
    ),
    EmbeddingModelInfo(
        model_id="nomic-ai/nomic-embed-text-v1",
        display_name="Nomic Embed Text v1",
        dimensions=768,
        popularity_rank=9,
        approx_downloads="~4.6M",
        description="Predecessor to v1.5, still widely used; long 8192-token context window for embedding larger documents.",
        languages="English",
        similarity_metric="cosine",
        text_prefix="search_document: ",
        approx_size_mb=550,
    ),
    EmbeddingModelInfo(
        model_id="sentence-transformers/all-MiniLM-L12-v2",
        display_name="all-MiniLM-L12-v2",
        dimensions=384,
        popularity_rank=10,
        approx_downloads="~3.4M",
        description="12-layer sibling of all-MiniLM-L6-v2 -- slightly slower, slightly more accurate.",
        languages="English",
        similarity_metric="cosine",
        approx_size_mb=130,
    ),
]

_BY_ID = {m.model_id: m for m in EMBEDDING_MODELS}


def get_model_info(model_id: str) -> EmbeddingModelInfo | None:
    return _BY_ID.get(model_id)


def list_models() -> list[EmbeddingModelInfo]:
    return list(EMBEDDING_MODELS)
