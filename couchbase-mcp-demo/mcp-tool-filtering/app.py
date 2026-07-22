import time
import asyncio
import json
import os
from typing import Optional, Dict, Any, List
from datetime import datetime
import logging

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from datetime import timedelta
import requests
from couchbase.cluster import Cluster
from couchbase.options import ClusterOptions, ClusterTimeoutOptions, UpsertOptions, SearchOptions, QueryOptions
from couchbase.auth import PasswordAuthenticator
from couchbase.exceptions import CouchbaseException, DocumentNotFoundException
import couchbase.search as cb_search
from couchbase.vector_search import VectorQuery as CBVectorQuery, VectorSearch as CBVectorSearch
import openai
import tiktoken
import numpy as np
import struct
import os

# Configure environment for optimal performance
os.environ["TOKENIZERS_PARALLELISM"] = "false"  # Prevent tokenizer warnings

from config import COUCHBASE_CONFIG, OLLAMA_CONFIG, DEMO_CONFIG, PERFORMANCE_CONFIG, LLM_PRICING
from tools_mcp_format import MCP_TOOLS_CONFIG


# Configure logging with performance optimization
log_level = getattr(logging, PERFORMANCE_CONFIG.get("log_level", "INFO").upper())
logging.basicConfig(level=log_level)
logger = logging.getLogger(__name__)
enable_timing_logs = PERFORMANCE_CONFIG.get("enable_timing_logs", True)

def calculate_llm_cost(provider: str, input_tokens: int, output_tokens: int) -> float:
    """
    The local Ollama model itself has no per-token API cost. This returns a
    reference/estimated cost - what this query would cost on a comparable
    hosted LLM API - using the rates in LLM_PRICING (config.py, overridable
    via LLM_INPUT_COST_PER_1M / LLM_OUTPUT_COST_PER_1M). This lets the token
    savings from Couchbase filtering show up as a dollar figure worth
    comparing between the two panels, even though nothing is actually billed.
    """
    input_cost = (input_tokens / 1_000_000) * LLM_PRICING["input_cost_per_1m"]
    output_cost = (output_tokens / 1_000_000) * LLM_PRICING["output_cost_per_1m"]
    return input_cost + output_cost

def extract_usage_tokens(response: Any, fallback_input_tokens: int, fallback_output_text: str, count_tokens_fn) -> tuple[int, int, int]:
    """Return prompt, completion, and total tokens from provider usage when available."""
    usage = getattr(response, "usage", None)
    if usage:
        prompt_tokens = getattr(usage, "prompt_tokens", None)
        completion_tokens = getattr(usage, "completion_tokens", None)
        total_tokens = getattr(usage, "total_tokens", None)
        if isinstance(prompt_tokens, int) and isinstance(completion_tokens, int):
            return prompt_tokens, completion_tokens, total_tokens or (prompt_tokens + completion_tokens)
    output_tokens = count_tokens_fn(fallback_output_text)
    return fallback_input_tokens, output_tokens, fallback_input_tokens + output_tokens

# Logging utilities with conditional evaluation for performance
def perf_log(message, *args):
    """Performance logging - only logs if timing logs enabled"""
    if enable_timing_logs and logger.isEnabledFor(logging.INFO):
        if args:
            logger.info(message % args)
        else:
            logger.info(message)

def debug_log(message, *args):
    """Debug logging - only logs if debug level enabled"""
    if logger.isEnabledFor(logging.DEBUG):
        if args:
            logger.debug(message % args)
        else:
            logger.debug(message)

# Initialize FastAPI application
app = FastAPI(
    title="Couchbase MCP Tool Filtering Demo",
    description="Branded MCP tool filtering demo powered by Couchbase vector search and Ollama.",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Mount logos directory
app.mount("/logos", StaticFiles(directory="logos"), name="logos")

# Embedding Service using SentenceTransformers
class ToolEmbeddings:
    """
    Optimized embedding generation with caching for maximum performance
    """
    def __init__(self, model_name='all-MiniLM-L6-v2'):
        """Initialize embedding model with performance optimizations"""
        try:
            from sentence_transformers import SentenceTransformer
            from functools import lru_cache
            import hashlib
            
            print(f"Loading embedding model: {model_name}...")
            self.model = SentenceTransformer(model_name)
            self.dimension = self.model.get_sentence_embedding_dimension()
            self.available = True
            
            # Initialize embedding cache for performance optimization
            self._embedding_cache = {}
            self._cache_hits = 0
            self._cache_misses = 0
            self._max_cache_size = PERFORMANCE_CONFIG.get("embedding_cache_size", 1000)
            
            print(f"Embedding model loaded successfully. Dimension: {self.dimension}, Cache size: {self._max_cache_size}")
        except ImportError as e:
            print(f"ERROR: sentence-transformers not installed: {e}")
            print("Install with: pip3 install sentence-transformers")
            raise ImportError("sentence-transformers is required for real embeddings")
        except Exception as e:
            print(f"ERROR: Failed to load embedding model: {e}")
            raise RuntimeError(f"Cannot initialize embedding model: {e}")
    
    def generate_embedding(self, text):
        """Generate embedding with caching for optimal performance"""
        if not text or not text.strip():
            raise ValueError("Text cannot be empty for embedding generation")
        
        # Create cache key from text hash
        import hashlib
        cache_key = hashlib.md5(text.encode('utf-8')).hexdigest()
        
        # Check cache first
        if cache_key in self._embedding_cache:
            self._cache_hits += 1
            return self._embedding_cache[cache_key]
        
        try:
            # Generate embedding
            embedding = self.model.encode(text, convert_to_numpy=True).astype(np.float32)
            
            # Cache with size limit (LRU-like behavior)
            if len(self._embedding_cache) >= self._max_cache_size:
                # Remove oldest entry
                oldest_key = next(iter(self._embedding_cache))
                del self._embedding_cache[oldest_key]
            
            self._embedding_cache[cache_key] = embedding
            self._cache_misses += 1
            return embedding
        except Exception as e:
            raise RuntimeError(f"SentenceTransformer embedding failed: {e}")
    
    def get_cache_stats(self):
        """Get embedding cache statistics"""
        total = self._cache_hits + self._cache_misses
        hit_rate = (self._cache_hits / total * 100) if total > 0 else 0
        return {
            "cache_hits": self._cache_hits,
            "cache_misses": self._cache_misses, 
            "hit_rate": round(hit_rate, 1),
            "cache_size": len(self._embedding_cache)
        }
    
    def embedding_to_bytes(self, embedding):
        """Convert numpy array to bytes (kept for compatibility/debugging use)"""
        if embedding is None:
            return None
        return struct.pack(f'{len(embedding)}f', *embedding)

    def bytes_to_embedding(self, bytes_data):
        """Convert bytes back to numpy array"""
        if not bytes_data:
            return None
        return np.array(struct.unpack(f'{len(bytes_data)//4}f', bytes_data))

    def normalized_vector(self, embedding) -> list:
        """
        L2-normalize an embedding and return it as a plain list of floats.

        Couchbase's Search vector index only supports dot_product / l2_norm
        similarity (no native cosine option), so we normalize vectors before
        storing/querying and use dot_product, which is mathematically
        equivalent to cosine similarity on unit vectors.
        """
        vec = np.asarray(embedding, dtype=np.float32)
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec.tolist()

class LLMService:
    """
    LLM service for intelligent tool selection, backed entirely by a local
    Ollama model via its OpenAI-compatible API (talking to Ollama, not
    OpenAI's hosted service). The demo uses two instances that both point at
    the same local model - one for the Unfiltered Approach (all tools) and
    one for the Couchbase-filtered approach (vector-prefiltered tools) - so
    the only variable between the two panels is how many tools are sent.
    """
    def __init__(self, role: str = "optimized"):
        self.client = None
        self.tokenizer = None
        self.role = role
        self.provider = "ollama"
        self.config = OLLAMA_CONFIG

    def initialize(self):
        """Initialize the Ollama OpenAI-compatible client and tokenizer."""
        try:
            self.client = openai.OpenAI(
                base_url=self.config["base_url"],
                api_key=self.config.get("api_key", "ollama")
            )
            # tiktoken has no notion of Ollama model names; cl100k_base gives a
            # close-enough token estimate for display purposes across models.
            self.tokenizer = tiktoken.get_encoding("cl100k_base")
            logger.info(f"Ollama client initialized ({self.role}) with model: {self.config['model']} at {self.config['base_url']}")
            return True
        except Exception as e:
            logger.error(f"Ollama initialization failed ({self.role}): {e}")
            return False
    
    def count_tokens(self, text: str) -> int:
        """
        Count tokens in text using tiktoken.
        
        Falls back to character-based estimation if tokenizer unavailable.
        
        Args:
            text: Text to tokenize
        
        Returns:
            Estimated token count
        """
        if not self.tokenizer:
            # Fallback estimation: ~4 characters per token
            return len(text) // 4
        return len(self.tokenizer.encode(text))
    
    def format_tools_for_llm(self, tools: List[Dict[str, Any]]) -> str:
        """
        Format MCP tools for LLM context.
        
        Creates a structured text representation of tools including
        their descriptions, parameters, and server information.
        
        Args:
            tools: List of tool dictionaries
        
        Returns:
            Formatted string for LLM context
        """
        formatted_tools = []
        for tool in tools:
            # Use full realistic tool definition for LLM context
            full_tool = None
            for server_name, server_tools in TOOLS_CONFIG.items():
                for t in server_tools:
                    if t["name"] == tool["name"]:
                        full_tool = t
                        break
                if full_tool:
                    break
            
            if not full_tool:
                continue
                
            tool_text = f"""Tool: {full_tool['name']}
Server: {tool.get('server', 'unknown')}
Type: {full_tool.get('type', 'read')}
Description: {full_tool['description']}"""
            
            # Handle MCP inputSchema format
            if 'inputSchema' in full_tool and 'properties' in full_tool['inputSchema']:
                tool_text += "\nParameters:\n"
                required_params = full_tool['inputSchema'].get('required', [])
                for param_name, param_info in full_tool['inputSchema']['properties'].items():
                    required_str = " (required)" if param_name in required_params else " (optional)"
                    tool_text += f"  - {param_name} ({param_info['type']}){required_str}: {param_info['description']}\n"
            
            formatted_tools.append(tool_text)
        
        return "\n\n".join(formatted_tools)
    
    async def select_relevant_tools(self, query: str, all_tools: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Use LLM to select relevant tools for a query.
        
        Sends tool definitions to the LLM and parses its response
        to identify the most appropriate tools for the given task.
        
        Args:
            query: User query requiring tool selection
        
        Returns:
            Dictionary containing selected tools and performance metrics
        """
        if not self.client:
            raise Exception(f"{self.provider.upper()} client not initialized")
        
        start_time = time.time()
        logger.info(f"LLM_SELECTION_START: provider={self.provider} tools_available={len(all_tools)} model={self.config['model']}")
        
        # Format all tools for LLM context
        tools_context = self.format_tools_for_llm(all_tools)
        context_length = len(tools_context)
        logger.info(f"LLM_CONTEXT_PREPARED: context_chars={context_length} tools_formatted={len(all_tools)}")
        
        # Count input tokens
        input_prompt = f"""You are an expert system administrator helping with operational tasks. Given the following query and available MCP tools, select the 1 most relevant tools that would be needed to address this request. Make sure you choose correctly review all the options provided to you, think deeply.

Query: {query}

Available Tools:
{tools_context}

Please respond with ONLY a JSON array of tool names that are most relevant to this query. Be selective - choose only the tools that are directly needed.

Example response format:
["tool.name1", "tool.name2", "tool.name3"]"""

        input_tokens = self.count_tokens(input_prompt)
        
        # Professional logging for demo transparency
        avg_tokens_per_tool = input_tokens / len(all_tools) if all_tools else 0
        logger.info(f"LLM_INPUT_PREPARED: input_tokens={input_tokens} query_length={len(query)}")
        logger.info(f"TOKEN_ANALYSIS: avg_tokens_per_tool={avg_tokens_per_tool:.1f} context_complexity=enterprise_scale")
        logger.info(f"REASONING_CHALLENGE: tools_to_evaluate={len(all_tools)} selection_complexity=high")
        
        try:
            response = await asyncio.to_thread(
                self.client.chat.completions.create,
                model=self.config["model"],
                messages=[{"role": "user", "content": input_prompt}],
                max_tokens=self.config["max_tokens"],
                temperature=self.config["temperature"],
                timeout=self.config.get("timeout", 120)
            )
            
            end_time = time.time()
            latency = round(end_time - start_time, 3)
            
            # Parse LLM response and use real provider usage metrics when returned.
            selected_tools_text = response.choices[0].message.content.strip()
            input_tokens, output_tokens, total_tokens = extract_usage_tokens(
                response,
                fallback_input_tokens=input_tokens,
                fallback_output_text=selected_tools_text,
                count_tokens_fn=self.count_tokens,
            )
            cost = calculate_llm_cost(self.provider, input_tokens, output_tokens)
            logger.info(
                f"LLM_USAGE: provider={self.provider} prompt_tokens={input_tokens} "
                f"completion_tokens={output_tokens} total_tokens={total_tokens} cost=${cost:.6f}"
            )
            
            try:
                import json
                # Clean up markdown code blocks if present
                clean_text = selected_tools_text.strip()
                if clean_text.startswith("```json"):
                    clean_text = clean_text[7:]  # Remove ```json
                if clean_text.endswith("```"):
                    clean_text = clean_text[:-3]  # Remove ```
                clean_text = clean_text.strip()
                
                selected_tool_names = json.loads(clean_text)
                
                # Ensure we have a list
                if not isinstance(selected_tool_names, list):
                    selected_tool_names = []
                
                # Find the full tool objects
                selected_tools = []
                for tool_name in selected_tool_names:
                    for tool in all_tools:
                        if tool["name"] == tool_name:
                            selected_tools.append(tool)
                            break
                
                # If no tools selected, use fallback
                if len(selected_tools) == 0 and len(all_tools) > 0:
                    logger.warning(f"LLM_NO_TOOLS_SELECTED: falling back to top {min(3, len(all_tools))} tools")
                    selected_tools = all_tools[:min(3, len(all_tools))]
                
                logger.info(f"LLM_SELECTION_COMPLETE: tools_selected={len(selected_tools)} tools_available={len(all_tools)} selection_rate={len(selected_tools)/len(all_tools):.2%}")
                
                # Professional analysis logging for demo transparency
                token_reduction = ((len(all_tools) - len(selected_tools)) / len(all_tools)) * 100 if all_tools else 0
                reasoning_efficiency = latency / len(all_tools) if all_tools else 0
                tokens_saved = (len(all_tools) - len(selected_tools)) * avg_tokens_per_tool if 'avg_tokens_per_tool' in locals() else 0
                
                logger.info(f"REASONING_PERFORMANCE: latency={latency}s efficiency={reasoning_efficiency:.3f}s_per_tool")
                logger.info(f"CONTEXT_REDUCTION: achieved={token_reduction:.1f}% tokens_saved={tokens_saved:.0f}")
                logger.info(f"PRODUCTION_IMPACT: reasoning_scales_with_tool_count=quadratic without_couchbase=bottleneck")
                
                # Log each selected tool
                for i, tool in enumerate(selected_tools, 1):
                    server = next((s for s, tools in TOOLS_CONFIG.items() for t in tools if t['name'] == tool['name']), 'unknown')
                    logger.info(f"LLM_SELECTED_TOOL: rank={i} name={tool['name']} server={server} type={tool['type']}")
                
                return {
                    "tools": selected_tools,
                    "latency": latency,
                    "tokens": total_tokens,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cost": cost,
                    "llm_response": selected_tools_text
                }
                
            except json.JSONDecodeError:
                logger.error(f"LLM_PARSE_ERROR: response_text={selected_tools_text[:200]}{'...' if len(selected_tools_text) > 200 else ''}")
                fallback_tools = all_tools[:5]
                logger.info(f"LLM_FALLBACK: tools_selected={len(fallback_tools)} method=first_n reason=parse_error")
                # Fallback to first 5 tools
                return {
                    "tools": fallback_tools,
                    "latency": latency,
                    "tokens": total_tokens,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cost": cost,
                    "llm_response": selected_tools_text,
                    "error": "Failed to parse LLM response"
                }
                
        except Exception as e:
            end_time = time.time()
            error_latency = round(end_time - start_time, 3)
            logger.error(f"LLM_API_ERROR: error={str(e)} latency={error_latency}s input_tokens={input_tokens if 'input_tokens' in locals() else 0}")
            raise

# Global service instances
cb_cluster = None
cb_bucket = None
cb_scope = None
tools_collection = None
cache_collection = None
llm_service = None
baseline_llm_service = None
is_couchbase_connected = False
tool_embeddings = None  # Real embedding service
tool_lookup_cache = {}  # Precomputed tool lookup cache

# Request/Response Models
class ChatRequest(BaseModel):
    query: str
    panel: str  # 'baseline' or 'optimized'

class ChatResponse(BaseModel):
    response: str
    latency: float
    tokens: int
    cost: float
    tools_count: int
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    cache_status: Optional[str] = None
    vector_search_time: Optional[int] = None
    similarity: Optional[int] = None
    original_query: Optional[str] = None
    tools_used: Optional[List[str]] = []
    filtered_tools: Optional[List[str]] = []

class HealthResponse(BaseModel):
    status: str
    couchbase: bool
    sentence_transformers: bool
    ollama: bool
    timestamp: str


# Load MCP tool definitions from configuration
TOOLS_CONFIG = MCP_TOOLS_CONFIG

def _search_index_definition(index_name: str, collection_name: str, text_fields: list, vector_field: str) -> dict:
    """
    Build a Couchbase Search (FTS) vector index definition, scoped to a single
    collection. `type_field` is set to a field name ("doc_type") that never
    appears on our documents, so the Search Service falls back to keying
    documents purely by "{scope}.{collection}" - i.e. every document in the
    target collection is indexed, with no extra type discrimination needed.
    """
    bucket = COUCHBASE_CONFIG["bucket"]
    scope = COUCHBASE_CONFIG["scope"]
    type_key = f"{scope}.{collection_name}"

    properties = {}
    for field_name in text_fields:
        properties[field_name] = {
            "dynamic": False,
            "enabled": True,
            "fields": [{
                "name": field_name,
                "type": "text",
                "analyzer": "standard",
                "index": True,
                "store": True,
            }]
        }
    properties[vector_field] = {
        "dynamic": False,
        "enabled": True,
        "fields": [{
            "name": vector_field,
            "type": "vector",
            "dims": PERFORMANCE_CONFIG["vector_dim"],
            "similarity": "dot_product",
            "index": True,
            "store": True,
        }]
    }

    return {
        "type": "fulltext-index",
        "name": f"{bucket}.{scope}.{index_name}",
        "sourceType": "gocbcore",
        "sourceName": bucket,
        "planParams": {"maxPartitionsPerPIndex": 512, "indexPartitions": 1},
        "params": {
            "doc_config": {
                "mode": "scope.collection.type_field",
                "type_field": "doc_type",
            },
            "mapping": {
                "default_analyzer": "standard",
                "default_datetime_parser": "dateTimeOptional",
                "default_field": "_all",
                "default_mapping": {"dynamic": False, "enabled": False},
                "default_type": "_default",
                "docvalues_dynamic": False,
                "index_dynamic": False,
                "store_dynamic": False,
                "type_field": "_type",
                "types": {
                    type_key: {"dynamic": False, "enabled": True, "properties": properties}
                }
            }
        },
        "store": {"indexType": "scorch", "segmentVersion": 16},
        "sourceParams": {},
    }


def _search_admin_url(index_name: str) -> str:
    host = COUCHBASE_CONFIG["search_host"]
    port = COUCHBASE_CONFIG["search_port"]
    bucket = COUCHBASE_CONFIG["bucket"]
    scope = COUCHBASE_CONFIG["scope"]
    return f"http://{host}:{port}/api/bucket/{bucket}/scope/{scope}/index/{index_name}"


def _upsert_search_index_sync(index_name: str, definition: dict):
    """Create a Search (FTS) vector index via the REST Admin API if it doesn't already exist."""
    auth = (COUCHBASE_CONFIG["username"], COUCHBASE_CONFIG["password"])
    url = _search_admin_url(index_name)
    existing = requests.get(url, auth=auth, timeout=10)
    if existing.status_code == 200:
        logger.info(f"Search index '{index_name}' already exists")
        return
    resp = requests.put(url, auth=auth, json=definition, timeout=15)
    if resp.status_code in (200, 201):
        logger.info(f"Search index '{index_name}' created")
    else:
        logger.warning(f"Search index '{index_name}' creation returned {resp.status_code}: {resp.text[:300]}")


async def initialize_couchbase():
    """
    Initialize the Couchbase connection, KV collections, and Search vector
    indexes used as the vector store (tools) and semantic cache.
    """
    global cb_cluster, cb_bucket, cb_scope, tools_collection, cache_collection, is_couchbase_connected

    try:
        def _connect():
            auth = PasswordAuthenticator(COUCHBASE_CONFIG["username"], COUCHBASE_CONFIG["password"])
            cluster = Cluster(
                COUCHBASE_CONFIG["connection_string"],
                ClusterOptions(auth, timeout_options=ClusterTimeoutOptions(kv_timeout=timedelta(seconds=10)))
            )
            cluster.wait_until_ready(timedelta(seconds=20))
            return cluster

        cb_cluster = await asyncio.to_thread(_connect)
        cb_bucket = cb_cluster.bucket(COUCHBASE_CONFIG["bucket"])
        cb_scope = cb_bucket.scope(COUCHBASE_CONFIG["scope"])
        tools_collection = cb_scope.collection(COUCHBASE_CONFIG["tools_collection"])
        cache_collection = cb_scope.collection(COUCHBASE_CONFIG["cache_collection"])

        is_couchbase_connected = True
        logger.info("Couchbase connection established successfully")

        await ensure_search_indexes()
        await index_tools_with_embeddings()

    except Exception as e:
        logger.warning(f"Couchbase connection failed: {e}")
        logger.info("Demo will run in mock mode")
        is_couchbase_connected = False


async def ensure_search_indexes():
    """
    Create the two Search (FTS) vector indexes if they don't already exist:
    1. Tools index - for finding relevant MCP tools
    2. Cache index - for semantic similarity matching of queries
    """
    try:
        tools_definition = _search_index_definition(
            COUCHBASE_CONFIG["tools_index"],
            COUCHBASE_CONFIG["tools_collection"],
            text_fields=["name", "description", "server", "type"],
            vector_field="embedding",
        )
        cache_definition = _search_index_definition(
            COUCHBASE_CONFIG["cache_index"],
            COUCHBASE_CONFIG["cache_collection"],
            text_fields=["query", "response", "tools_used", "cached_at"],
            vector_field="embedding",
        )

        await asyncio.to_thread(_upsert_search_index_sync, COUCHBASE_CONFIG["tools_index"], tools_definition)

        # Preserve existing cache data - only create the cache index, never drop it here.
        existing_cache_count = await _count_documents(COUCHBASE_CONFIG["cache_collection"])
        if existing_cache_count:
            logger.info(f"Cache index already exists with {existing_cache_count} cached items - preserving all data")
        await asyncio.to_thread(_upsert_search_index_sync, COUCHBASE_CONFIG["cache_index"], cache_definition)

        logger.info("Couchbase Search vector indexes initialized successfully")

    except Exception as e:
        logger.warning(f"Couchbase Search index setup failed: {e}")


async def _count_documents(collection_name: str) -> int:
    """Run a N1QL COUNT(*) over a collection. Requires a primary index (created at provisioning time)."""
    if not is_couchbase_connected or not cb_cluster:
        return 0
    bucket = COUCHBASE_CONFIG["bucket"]
    scope = COUCHBASE_CONFIG["scope"]

    def _run():
        query = f"SELECT RAW COUNT(*) FROM `{bucket}`.`{scope}`.`{collection_name}`"
        result = cb_cluster.query(query, QueryOptions(metrics=False))
        rows = list(result.rows())
        return rows[0] if rows else 0

    try:
        return await asyncio.to_thread(_run)
    except Exception as e:
        debug_log("COUNT_QUERY_FAILED: collection=%s error=%s", collection_name, str(e))
        return 0


async def index_tools_with_embeddings():
    """
    Index all MCP tools with their embeddings in Couchbase.
    Only indexes if tools are not already present in the tools collection.
    """
    if not is_couchbase_connected or not tools_collection:
        logger.warning("Skipping tool indexing: Couchbase not connected or tools collection missing")
        return

    try:
        existing_count = await _count_documents(COUCHBASE_CONFIG["tools_collection"])
        expected_count = sum(len(tools) for tools in TOOLS_CONFIG.values())

        if existing_count >= expected_count:
            logger.info(f"Tools already indexed ({existing_count} found) - skipping reindexing")
            return

        logger.info(f"Indexing tools: found {existing_count}, expected {expected_count}")
        tool_data = []
        embedding_stats = {"sentence_transformers": 0}

        logger.info(f"Starting tool embedding generation for {expected_count} tools...")

        # Generate real embeddings for all tools using enhanced text
        for server_name, tools in TOOLS_CONFIG.items():
            perf_log("EMBEDDING_GENERATION: Processing %s server with %d tools", server_name, len(tools))

            for tool in tools:
                tool_text = generate_enhanced_embedding_text(tool, server_name)

                if tools.index(tool) == 0:
                    debug_log("EMBEDDING_SAMPLE: %s tool text length=%d chars", server_name, len(tool_text))

                try:
                    embedding_array = tool_embeddings.generate_embedding(tool_text)
                    embedding = tool_embeddings.normalized_vector(embedding_array)
                    embedding_stats["sentence_transformers"] = embedding_stats.get("sentence_transformers", 0) + 1
                    logger.debug(f"Generated SentenceTransformer embedding for {tool['name']} (dimensions: {len(embedding)})")
                except Exception as e:
                    logger.error(f"CRITICAL: Embedding generation failed for {tool['name']}: {e}")
                    raise RuntimeError(f"Cannot generate embedding for tool {tool['name']}: {e}")

                if not isinstance(embedding, list) or len(embedding) != PERFORMANCE_CONFIG["vector_dim"]:
                    logger.error(f"Invalid embedding for {tool['name']}: type={type(embedding)}, len={len(embedding) if hasattr(embedding, '__len__') else 'N/A'}")
                    continue

                tool_doc = {
                    "name": tool["name"],
                    "description": tool["description"],
                    "server": server_name,
                    "type": tool["type"],
                    "embedding": embedding
                }

                tool_data.append(tool_doc)

        stats_summary = []
        if embedding_stats.get("sentence_transformers", 0) > 0:
            stats_summary.append(f"{embedding_stats['sentence_transformers']} SentenceTransformers")

        logger.info(f"Embedding generation complete: {' + '.join(stats_summary)} = {len(tool_data)} total")

        # Store as native JSON documents in the tools collection (Couchbase KV)
        logger.info("Storing tool embeddings in Couchbase...")

        def _store_all():
            stored = 0
            for doc in tool_data:
                try:
                    tools_collection.upsert(doc["name"], doc)
                    stored += 1
                except Exception as store_error:
                    logger.error(f"Failed to store {doc['name']}: {store_error}")
            return stored

        stored_count = await asyncio.to_thread(_store_all)
        logger.info(f"Storage complete: {stored_count}/{len(tool_data)} tools stored with vector embeddings")

        # Test vector search on stored data
        logger.info("Testing vector search functionality...")
        try:
            test_embedding_array = tool_embeddings.generate_embedding("test query")
            test_vector = tool_embeddings.normalized_vector(test_embedding_array)
            test_results = await vector_search_tools_raw(test_vector, top_k=3)
            logger.info(f"Search test successful: Found {len(test_results)} results")
            for i, result in enumerate(test_results[:2]):
                logger.info(f"  {i+1}. {result.get('name', 'N/A')} (similarity: {result.get('similarity', 'N/A')})")
        except Exception as test_error:
            logger.error(f"Search test failed: {test_error}")

        logger.info(f"Tool indexing complete: {len(tool_data)} tools ready for vector search")

    except Exception as e:
        logger.error(f"Critical tool indexing error: {e}")
        logger.info("Falling back to mock tool selection for demo reliability")

def generate_enhanced_embedding_text(tool: Dict[str, Any], server_name: str) -> str:
    """Generate enhanced text for tool embeddings using natural language expansion."""

    # Start with the tool name and full description
    text_parts = [
        tool['name'],
        tool['description']
    ]

    # Add server/service context with domain-specific keywords
    server_context = {
        "zendesk": "customer support helpdesk ticketing customer service external customers end-users",
        "jira": "project management internal issues development bugs tasks sprint agile",
        "hubspot": "sales marketing CRM deals leads pipeline revenue",
        "pagerduty": "incident response on-call alerts engineering teams escalation",
        "datadog": "application monitoring APM logs metrics observability infrastructure",
        "confluence": "documentation wiki knowledge base articles pages collaboration",
        "m365": "microsoft office email teams sharepoint outlook calendar",
        "snowflake": "data warehouse SQL analytics database queries reporting"
    }

    if server_name.lower() in server_context:
        text_parts.append(server_context[server_name.lower()])

    text_parts.append(f"This {server_name} tool performs {tool['type']} operations.")
    
    # Include parameter information from inputSchema (MCP format)
    if "inputSchema" in tool and "properties" in tool["inputSchema"]:
        param_descriptions = []
        properties = tool["inputSchema"]["properties"]
        
        # Extract meaningful parameter descriptions
        for param_name, param_info in properties.items():
            if isinstance(param_info, dict) and "description" in param_info:
                # Include parameter descriptions which often contain valuable context
                param_descriptions.append(f"{param_name}: {param_info['description']}")
            
            # Also check nested properties for richer context
            if isinstance(param_info, dict) and "properties" in param_info:
                for nested_name, nested_info in param_info["properties"].items():
                    if isinstance(nested_info, dict) and "description" in nested_info:
                        param_descriptions.append(f"{nested_name}: {nested_info['description']}")
        
        if param_descriptions:
            # Add key parameter descriptions which contain use cases and context
            text_parts.extend(param_descriptions[:5])  # Include more context
    
    # Extract semantic keywords from tool name
    tool_name_parts = tool['name'].split('.')
    if len(tool_name_parts) > 1:
        action = tool_name_parts[1].replace('_', ' ')
        text_parts.append(f"Action: {action}")
    
    # Add common use case keywords based on tool function and server context
    tool_lower = tool['name'].lower()
    desc_lower = tool['description'].lower()

    if 'search' in tool_lower:
        text_parts.append("search query filter find lookup retrieve")
    if 'ticket' in tool_lower or 'ticket' in desc_lower:
        if server_name.lower() == 'zendesk':
            text_parts.append("customer tickets support requests customer issues helpdesk")
        elif server_name.lower() == 'hubspot':
            text_parts.append("service hub sales tickets deals pipeline")
    if 'log' in tool_lower:
        text_parts.append("logs logging events errors exceptions")
    if 'incident' in tool_lower or 'incident' in desc_lower:
        text_parts.append("incident outage critical emergency production issue")
    if 'trace' in tool_lower:
        text_parts.append("tracing spans performance monitoring")
    if 'performance' in desc_lower:
        text_parts.append("performance metrics latency errors throughput")
    if 'error' in desc_lower or 'failure' in desc_lower:
        text_parts.append("error debugging failure investigation troubleshooting")
    if 'claims' in desc_lower:
        text_parts.append("claims transactions policy financial money")
    if 'customer' in desc_lower:
        if server_name.lower() == 'zendesk':
            text_parts.append("external customers end users support requests")
        elif server_name.lower() == 'hubspot':
            text_parts.append("leads prospects sales opportunities")
    
    # The full text naturally contains the semantic meaning
    return " ".join(text_parts)

def _run_vector_search_sync(index_name: str, vector: list, top_k: int, return_fields: list) -> list:
    """Execute a Couchbase Search vector query synchronously (called via asyncio.to_thread)."""
    vector_query = CBVectorQuery.create("embedding", vector, num_candidates=max(top_k, 1))
    vector_search = CBVectorSearch.from_vector_query(vector_query)
    request = cb_search.SearchRequest.create(vector_search)
    result = cb_scope.search(
        index_name,
        request,
        SearchOptions(limit=top_k, fields=return_fields + ["embedding"])
    )
    rows = []
    for row in result.rows():
        rows.append({"id": row.id, "fields": row.fields or {}})
    return rows


async def vector_search_tools_raw(query_vector: list, top_k: int = 3) -> List[Dict[str, Any]]:
    """
    Run the tools vector search given an already-normalized query embedding.
    Similarity is computed as a dot product against the (also normalized)
    stored embedding, which is mathematically equivalent to cosine similarity.
    """
    rows = await asyncio.to_thread(
        _run_vector_search_sync,
        COUCHBASE_CONFIG["tools_index"],
        query_vector,
        top_k,
        ["name", "description", "server", "type"],
    )

    query_np = np.array(query_vector, dtype=np.float32)
    selected_tools = []
    for row in rows:
        fields = row["fields"]
        stored_vector = fields.get("embedding")
        similarity_score = round(float(np.dot(query_np, np.array(stored_vector, dtype=np.float32))), 3) if stored_vector else 0.0
        selected_tools.append({
            "name": fields.get("name"),
            "description": fields.get("description"),
            "server": fields.get("server"),
            "type": fields.get("type"),
            "similarity": similarity_score,
        })

    selected_tools.sort(key=lambda t: t["similarity"], reverse=True)
    return selected_tools


async def vector_search_tools(query: str, top_k: int = 3) -> List[Dict[str, Any]]:
    start_time = time.time() if enable_timing_logs else None

    perf_log("VECTOR_SEARCH_START: query_length=%d top_k=%d", len(query), top_k)
    debug_log("QUERY_TEXT: %s", query[:200] + ('...' if len(query) > 200 else ''))

    try:
        embedding_start = time.time() if enable_timing_logs else None

        try:
            embedding_array = tool_embeddings.generate_embedding(query)
            query_embedding = tool_embeddings.normalized_vector(embedding_array)
            if enable_timing_logs:
                embedding_time = int((time.time() - embedding_start) * 1000)
                perf_log("EMBEDDING_GENERATED: method=SentenceTransformer time_ms=%d dimensions=%d", embedding_time, len(query_embedding))
        except Exception as e:
            logger.error(" CRITICAL: Query embedding generation failed: %s", e)
            raise RuntimeError(f"Cannot generate embedding for query '{query}': {e}")

        # Validate embedding
        if not isinstance(query_embedding, list) or len(query_embedding) != PERFORMANCE_CONFIG["vector_dim"]:
            logger.error(f" EMBEDDING: Invalid query embedding: type={type(query_embedding)}, len={len(query_embedding) if hasattr(query_embedding, '__len__') else 'N/A'}")
            return []

        # Create and execute the Couchbase Search vector query
        search_start = time.time() if enable_timing_logs else None
        debug_log("COUCHBASE_QUERY_START: method=SearchVectorIndex vector_field=embedding return_fields=4")

        try:
            selected_tools_raw = await vector_search_tools_raw(query_embedding, top_k=top_k)
        except Exception as e:
            logger.error(f"COUCHBASE_QUERY_ERROR: {e}")
            selected_tools_raw = []

        if enable_timing_logs:
            search_time = int((time.time() - search_start) * 1000)
            perf_log("COUCHBASE_QUERY_COMPLETE: time_ms=%d results_count=%d", search_time, len(selected_tools_raw))
        else:
            search_time = 0

        # Attach search timing to each result
        selected_tools = []
        for i, result in enumerate(selected_tools_raw):
            result["search_time_ms"] = int((time.time() - start_time) * 1000) if start_time else 0
            selected_tools.append(result)
            logger.info(f"TOOL_RANKED: rank={i+1} name={result['name']} server={result['server']} similarity={result['similarity']}")

        if enable_timing_logs and start_time:
            total_time = int((time.time() - start_time) * 1000)
            logger.info(f"VECTOR_SEARCH_SUCCESS: tools_selected={len(selected_tools)} total_time_ms={total_time}")
        else:
            total_time = 0

        if selected_tools:
            best_tool = selected_tools[0]
            worst_tool = selected_tools[-1] if len(selected_tools) > 1 else best_tool
            logger.info(f"SIMILARITY_RANGE: best={best_tool['similarity']:.3f} worst={worst_tool['similarity']:.3f}")
            logger.info(f"TOP_RESULT: name={best_tool['name']} server={best_tool['server']} similarity={best_tool['similarity']:.3f}")

        return selected_tools

    except Exception as e:
        if enable_timing_logs and start_time:
            total_time = int((time.time() - start_time) * 1000)
            logger.error(f"VECTOR_SEARCH_ERROR: error={str(e)} time_ms={total_time}")
        else:
            total_time = 0
            logger.error(f"VECTOR_SEARCH_ERROR: error={str(e)}")
        logger.info("FALLBACK_ACTIVATED: method=rule_based reason=vector_search_failed")
        return []

async def check_semantic_cache(query: str) -> Optional[Dict[str, Any]]:
    """Optimized semantic cache check with performance improvements"""
    
    # Fast path: Check if caching is enabled
    if not PERFORMANCE_CONFIG.get("enable_semantic_cache", True):
        return None
    
    # Only check cache for information requests
    if not is_information_request(query):
        debug_log("CACHE_CHECK_SKIP: Not an information request: '%s'", query)
        return None
        
    if not is_couchbase_connected or not cache_collection:
        debug_log("CACHE_UNAVAILABLE: Couchbase not connected or cache collection missing")
        return None

    try:
        # Generate real embedding for the query using SentenceTransformers
        try:
            embedding_array = tool_embeddings.generate_embedding(query)
            query_embedding = tool_embeddings.normalized_vector(embedding_array)
        except Exception as e:
            logger.error(f" CRITICAL: Cache embedding generation failed: {e}")
            raise RuntimeError(f"Cannot generate cache embedding for query: {e}")

        # Search for similar cached queries via the cache Search vector index
        rows = await asyncio.to_thread(
            _run_vector_search_sync,
            COUCHBASE_CONFIG["cache_index"],
            query_embedding,
            1,
            ["query", "response", "tools_used", "cached_at"],
        )

        if rows:
            fields = rows[0]["fields"]
            stored_vector = fields.get("embedding")
            similarity = float(np.dot(np.array(query_embedding, dtype=np.float32), np.array(stored_vector, dtype=np.float32))) if stored_vector else 0.0
            threshold = PERFORMANCE_CONFIG["cache_similarity_threshold"]

            logger.info(f"CACHE_SIMILARITY_CHECK: similarity={similarity:.3f} threshold={threshold:.3f} query='{fields.get('query', 'N/A')}'")

            # Check if similarity meets threshold
            if similarity >= threshold:
                logger.info(f"CACHE_HIT: {similarity:.0%} similarity with cached query='{fields.get('query', 'N/A')}'")
                tools_used_raw = fields.get("tools_used", "[]")
                tools_used = json.loads(tools_used_raw) if isinstance(tools_used_raw, str) else tools_used_raw
                return {
                    "response": fields.get("response"),
                    "similarity": int(similarity * 100),
                    "cached_at": fields.get("cached_at"),
                    "original_query": fields.get("query", "Previous similar query"),
                    "tools_used": tools_used
                }
            else:
                logger.info(f"CACHE_MISS: similarity={similarity:.3f} below threshold={threshold:.3f}")

        # No cache hit found
        logger.info("CACHE_MISS: No similar cached queries found")
        return None

    except Exception as e:
        logger.error(f"Cache check error: {e}")
        return None


def is_information_request(query: str) -> bool:
    """Check if the query is requesting information (cacheable)."""
    query_lower = query.lower().strip()
    
    # Information request keywords
    info_keywords = [
        "get", "search", "find", "show", "list", "display",
        "what", "where", "when", "why", "how", "which", "who",
        "tell me", "give me", "fetch", "retrieve", "look up"
    ]
    
    # Check if query starts with or contains information request patterns
    for keyword in info_keywords:
        if query_lower.startswith(keyword) or f" {keyword} " in f" {query_lower} ":
            return True
    
    # Also check for question patterns
    if query_lower.endswith("?"):
        return True
        
    return False

async def store_in_cache(query: str, response: str, tools_used: List[str]):
    """Optimized cache storage with performance improvements"""
    
    # Fast path: Check if caching is enabled  
    if not PERFORMANCE_CONFIG.get("enable_semantic_cache", True):
        return
    
    # Only cache information requests
    if not is_information_request(query):
        debug_log("CACHE_STORE_SKIP: Not an information request: '%s'", query)
        return
        
    debug_log("CACHE_STORE: storing information request='%s' with %d tools", query, len(tools_used))
    
    if not is_couchbase_connected or not cache_collection:
        debug_log("CACHE_STORE_SKIP: Couchbase not connected or cache collection missing")
        return

    try:
        # Generate real embedding for the query using SentenceTransformers
        try:
            embedding_array = tool_embeddings.generate_embedding(query)
            query_embedding = tool_embeddings.normalized_vector(embedding_array)
        except Exception as e:
            logger.error(f" CRITICAL: Cache storage embedding generation failed: {e}")
            raise RuntimeError(f"Cannot generate embedding for caching: {e}")

        cache_key = f"cache::{abs(hash(query)) % 10000}"

        cache_data = {
            "query": query,
            "response": response,
            "tools_used": json.dumps(tools_used),
            "cached_at": datetime.now().isoformat(),
            "embedding": query_embedding
        }

        # Store in Couchbase with a document TTL (expiry) for reliability
        def _store():
            cache_collection.upsert(
                cache_key,
                cache_data,
                UpsertOptions(expiry=timedelta(seconds=PERFORMANCE_CONFIG["cache_ttl"]))
            )

        await asyncio.to_thread(_store)

        logger.info(f"✅ CACHE_STORED: query='{query}' key={cache_key}")

    except Exception as e:
        logger.error(f"Cache storage error: {e}")


def format_tool_selection_response(selected_tools: List[Dict[str, Any]], method: str, total_tools: int) -> str:
    """
    Format tool selection results for display.
    
    Creates a concise summary showing reduction in tool count
    and selection efficiency.
    
    Args:
        selected_tools: List of selected tools
        method: Selection method (baseline or optimized)
        total_tools: Total number of available tools
    
    Returns:
        Formatted summary string
    """
    if not selected_tools:
        return f"{method.upper()}: No tools selected"
    
    selection_rate = (len(selected_tools) / total_tools) * 100 if total_tools > 0 else 0
    
    # Simple, professional summary
    summary = f"{method.upper()}: {len(selected_tools)} of {total_tools} tools selected"
    
    if method == "optimized":
        reduction = 100 - selection_rate
        summary += f" ({reduction:.0f}% reduction)"
    
    return summary

def is_write_operation(query: str) -> bool:
    """Check if query involves write operations."""
    write_keywords = ["create", "send", "update", "add", "delete", "draft and send"]
    return any(keyword in query.lower() for keyword in write_keywords)

async def process_baseline_query(query: str) -> ChatResponse:
    """
    Process query using baseline approach with all tools sent to LLM.
    
    This represents the traditional MCP approach where all available
    tools are sent to the LLM for reasoning, resulting in higher
    latency and token usage.
    
    Args:
        query: User query to process
    
    Returns:
        ChatResponse with tool selection results and metrics
    """
    if not baseline_llm_service or not baseline_llm_service.client:
        raise HTTPException(status_code=503, detail="LLM service not available. Ensure Ollama is running and OLLAMA_MODEL is pulled, then restart the app.")
    
    start_time = time.time()
    logger.info(f"BASELINE_QUERY_START: query_length={len(query)} approach=all_tools_to_llm")
    
    # Get all tools with full realistic definitions
    all_tools = []
    for server_name, server_tools in TOOLS_CONFIG.items():
        for tool in server_tools:
            all_tools.append({**tool, "server": server_name})
    
    logger.info(f"BASELINE_TOOLS_LOADED: total_tools={len(all_tools)} servers={len(TOOLS_CONFIG)}")
    
    try:
        # Call LLM with ALL tools - this is the baseline (expensive)
        logger.info(f"BASELINE_LLM_CALL: sending_all_tools={len(all_tools)} to LLM")
        llm_result = await baseline_llm_service.select_relevant_tools(query, all_tools)
        
        # Show actual LLM tool selection results instead of mock business response
        response_text = format_tool_selection_response(llm_result["tools"], "baseline", len(all_tools))
        
        end_time = time.time()
        actual_latency = round(end_time - start_time, 3)
        
        logger.info(f"BASELINE_COMPLETE: latency={actual_latency}s tokens={llm_result['tokens']} cost=${llm_result['cost']:.4f} tools_sent={len(all_tools)} tools_selected={len(llm_result['tools'])}")
        
        # Professional workflow analysis
        logger.info(f"WORKFLOW_ANALYSIS: phase=tool_selection method=baseline status=complete")
        logger.info(f"NEXT_PHASE: mcp_tool_execution tools_to_execute={len(llm_result['tools'])} estimated_time={len(llm_result['tools'])*2.5:.1f}s")
        logger.info(f"COUCHBASE_VALUE_PROP: current_bottleneck=llm_reasoning scales_poorly=yes solution=vector_prefiltering")
        
        return ChatResponse(
            response=response_text,
            latency=actual_latency,
            tokens=llm_result["tokens"],
            cost=round(llm_result["cost"], 6),
            tools_count=len(all_tools),  # All tools sent to LLM
            input_tokens=llm_result.get("input_tokens"),
            output_tokens=llm_result.get("output_tokens"),
            provider="ollama",
            model=baseline_llm_service.config.get("model"),
            tools_used=[tool["name"] for tool in llm_result["tools"]],  # LLM selected tools
            cache_status="BYPASS"
        )
        
    except Exception as e:
        end_time = time.time()
        error_latency = round(end_time - start_time, 3)
        logger.error(f"BASELINE_ERROR: error={str(e)} latency={error_latency}s")
        raise HTTPException(status_code=500, detail=str(e))

async def process_optimized_query(query: str) -> ChatResponse:
    """
    Process query using the Couchbase-optimized approach.

    Uses vector search to pre-filter relevant tools before sending
    to LLM, and semantic caching for similar queries. This approach
    significantly reduces latency and token usage.
    
    Args:
        query: User query to process
    
    Returns:
        ChatResponse with tool selection results and metrics
    """
    if not llm_service or not llm_service.client:
        raise HTTPException(status_code=503, detail="LLM service not available")
    
    start_time = time.time()
    logger.info(f"OPTIMIZED_QUERY_START: query_length={len(query)} approach=vector_search_plus_cache")
    
    # Parallel cache check + vector search preparation for optimal latency
    cache_start = time.time()
    total_available = sum(len(tools) for tools in TOOLS_CONFIG.values())
    
    # Start both cache check and vector search in parallel (cache is usually faster)
    cache_task = asyncio.create_task(check_semantic_cache(query))
    # Pre-calculate for potential vector search
    
    cached_result = await cache_task
    cache_check_time = int((time.time() - cache_start) * 1000)
    logger.info(f"CACHE_CHECK_COMPLETE: time_ms={cache_check_time} result={'HIT' if cached_result else 'MISS'}")
    
    if cached_result:
        # Cache hit - return immediately with real timing
        cache_end_time = time.time()
        cache_latency = round(cache_end_time - start_time, 3)
        
        logger.info(f"CACHE_HIT_RETURN: latency={cache_latency}s similarity={cached_result.get('similarity')}% tokens_saved=significant cost_saved=significant")
        
        return ChatResponse(
            response=cached_result["response"],
            latency=cache_latency,
            tokens=0,  # Cached response - no LLM call
            cost=0.0,
            tools_count=0,
            cache_status="HIT",
            vector_search_time=int(cache_latency * 1000),
            similarity=cached_result.get("similarity"),
            original_query=cached_result.get("original_query")
        )
    
    # Cache miss - now do vector search
    vector_start = time.time()
    logger.info(f"VECTOR_FILTERING_START: total_available_tools={total_available}")
    
    vector_filtered_tools = await vector_search_tools(query)
    vector_end_time = time.time()
    vector_time = int((vector_end_time - vector_start) * 1000)
    
    logger.info(f"VECTOR_FILTERING_COMPLETE: tools_reduced_from={total_available}_to={len(vector_filtered_tools)} reduction_ratio={len(vector_filtered_tools)/total_available:.1%} time_ms={vector_time}")
    
    # Convert to format needed for LLM (optimized lookup)
    filtered_for_llm = []
    # Use precomputed global tool lookup cache - O(1) lookup
    for tool in vector_filtered_tools:
        if tool["name"] in tool_lookup_cache:
            filtered_for_llm.append(tool_lookup_cache[tool["name"]])
    
    try:
        # Call LLM with ONLY the pre-filtered tools (much fewer than baseline)
        logger.info(f"OPTIMIZED_LLM_CALL: sending_filtered_tools={len(filtered_for_llm)} reduction_from={total_available}")
        llm_result = await llm_service.select_relevant_tools(query, filtered_for_llm)
        
        # Show actual LLM tool selection results instead of mock business response
        response_text = format_tool_selection_response(llm_result["tools"], "optimized", len(filtered_for_llm))
        
        end_time = time.time()
        actual_latency = round(end_time - start_time, 3)
        
        # Store in cache if it's a read operation (for next time)
        will_cache = not is_write_operation(query)
        if will_cache:
            await store_in_cache(query, response_text, [tool["name"] for tool in llm_result["tools"]])
        
        # Determine cache status
        cache_status = "BYPASS" if is_write_operation(query) else "MISS"
        
        logger.info(f"OPTIMIZED_COMPLETE: latency={actual_latency}s tokens={llm_result['tokens']} cost=${llm_result['cost']:.4f} tools_sent={len(filtered_for_llm)} tools_selected={len(llm_result['tools'])} cache_status={cache_status} will_cache={will_cache}")
        
        # Professional workflow analysis  
        efficiency_gain = (1 - (len(filtered_for_llm) / total_available)) * 100 if total_available > 0 else 0
        logger.info(f"WORKFLOW_ANALYSIS: phase=tool_selection method=couchbase_optimized status=complete efficiency_gain={efficiency_gain:.1f}%")
        logger.info(f"NEXT_PHASE: mcp_tool_execution tools_to_execute={len(llm_result['tools'])} estimated_time={len(llm_result['tools'])*2.5:.1f}s")
        logger.info(f"COUCHBASE_IMPACT: prefiltering_reduced_context_by={efficiency_gain:.1f}% reasoning_time_saved={actual_latency:.1f}s")
        
        return ChatResponse(
            response=response_text,
            latency=actual_latency,
            tokens=llm_result["tokens"],
            cost=round(llm_result["cost"], 6),
            tools_count=len(vector_filtered_tools),  # Actual vector search results count
            input_tokens=llm_result.get("input_tokens"),
            output_tokens=llm_result.get("output_tokens"),
            provider="ollama",
            model=llm_service.config.get("model"),
            cache_status=cache_status,
            vector_search_time=vector_time,
            tools_used=[tool["name"] for tool in llm_result["tools"]],
            filtered_tools=[tool["name"] for tool in vector_filtered_tools]
        )
        
    except Exception as e:
        end_time = time.time()
        error_latency = round(end_time - start_time, 3)
        logger.error(f"OPTIMIZED_ERROR: error={str(e)} latency={error_latency}s vector_time_ms={vector_time if 'vector_time' in locals() else 0}")
        raise HTTPException(status_code=500, detail=str(e))

def initialize_baseline_ollama_service() -> bool:
    """Initialize or reinitialize the local Ollama service used by the Unfiltered Approach."""
    global baseline_llm_service
    baseline_llm_service = LLMService(role="baseline")
    initialized = baseline_llm_service.initialize()
    if initialized:
        logger.info("Ollama LLM ready for Unfiltered Approach")
    else:
        logger.warning("Unfiltered Approach disabled - ensure Ollama is running and OLLAMA_MODEL is pulled")
    return initialized


@app.on_event("startup")
async def startup_event():
    """
    Initialize all services on application startup.
    
    Sets up:
    - Embedding service (SentenceTransformers)
    - Couchbase connection, collections, and Search vector indexes
    - Tool lookup cache
    - LLM service (Ollama)
    """
    logger.info("Initializing Couchbase MCP Tool Filtering Demo...")
    
    # Initialize real embedding service - REQUIRED
    global tool_embeddings
    try:
        logger.info("Initializing SentenceTransformers embedding service...")
        tool_embeddings = ToolEmbeddings()
        logger.info(f"SentenceTransformers ready: {tool_embeddings.dimension}-dimensional embeddings")
        # Update config with actual dimension
        PERFORMANCE_CONFIG["vector_dim"] = tool_embeddings.dimension
        logger.info("Real-time embeddings enabled")
    except Exception as e:
        logger.error(f"CRITICAL: Embedding service initialization failed: {e}")
        logger.error("This demo requires real embeddings with sentence-transformers")
        logger.error("Install with: pip3 install sentence-transformers")
        raise RuntimeError("Cannot start demo without real embedding service")
    
    # Initialize global tool lookup cache for O(1) performance
    global tool_lookup_cache
    tool_lookup_cache = {}
    for server_name, server_tools in TOOLS_CONFIG.items():
        for tool in server_tools:
            tool_lookup_cache[tool["name"]] = {**tool, "server": server_name}
    logger.info(f"Tool lookup cache initialized with {len(tool_lookup_cache)} tools")
    
    # Initialize Couchbase
    await initialize_couchbase()

    # Initialize LLM services for real tool selection - both backed by the same local Ollama model
    global llm_service, baseline_llm_service
    try:
        logger.info("Initializing Ollama LLM service for Unfiltered Approach...")
        initialize_baseline_ollama_service()

        logger.info("Initializing Ollama LLM service for Couchbase-filtered approach...")
        llm_service = LLMService(role="optimized")
        llm_initialized = llm_service.initialize()
        if llm_initialized:
            logger.info("Ollama LLM ready for Couchbase-filtered tool selection")
        else:
            logger.warning("Couchbase-filtered LLM service disabled - ensure Ollama is running and OLLAMA_MODEL is pulled")
    except Exception as e:
        logger.warning(f"LLM service initialization failed: {e}")
        logger.info("Demo will use fallback tool selection without LLM")
    
    logger.info("Demo initialization complete")

@app.get("/")
async def serve_index():
    """Serve the main demo page."""
    return FileResponse("static/index.html")

@app.get("/api/health")
async def health_check() -> HealthResponse:
    """
    Service health check endpoint.
    
    Returns status of all critical components including Couchbase,
    embedding service, and LLM connectivity.
    """
    return HealthResponse(
        status="ok",
        couchbase=is_couchbase_connected,
        sentence_transformers=tool_embeddings is not None,
        ollama=(llm_service is not None and llm_service.client is not None
                and baseline_llm_service is not None and baseline_llm_service.client is not None),
        timestamp=datetime.now().isoformat()
    )

@app.get("/api/events")
async def events():
    """Compatibility endpoint for clients/extensions that open an SSE stream."""
    async def event_generator():
        yield "event: ready\ndata: {\"status\": \"ok\"}\n\n"
        while True:
            await asyncio.sleep(15)
            yield "event: ping\ndata: {}\n\n"
    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/api/settings")
async def get_settings():
    """Return runtime LLM settings needed by the demo UI."""
    ollama_configured = (llm_service is not None and llm_service.client is not None
                          and baseline_llm_service is not None and baseline_llm_service.client is not None)
    return {
        "ollama_configured": ollama_configured,
        "ollama_model": OLLAMA_CONFIG.get("model"),
    }


@app.post("/api/settings/ollama/reconnect")
async def reconnect_ollama():
    """Retry connecting both LLM service instances to the local Ollama server.

    Useful if the demo started before Ollama finished pulling OLLAMA_MODEL -
    no API key or other configuration is needed since everything runs locally.
    """
    global llm_service
    baseline_ok = initialize_baseline_ollama_service()
    llm_service = LLMService(role="optimized")
    optimized_ok = llm_service.initialize()

    if not (baseline_ok and optimized_ok):
        raise HTTPException(status_code=503, detail="LLM still not reachable. Ensure the ollama service is healthy and OLLAMA_MODEL is pulled.")
    return {
        "ollama_configured": True,
        "ollama_model": OLLAMA_CONFIG.get("model"),
        "message": "Reconnected to local LLM for both approaches"
    }


@app.post("/api/query")
async def process_query(request: ChatRequest) -> ChatResponse:
    """
    Process user query using selected approach.
    
    Routes to either baseline (all tools) or optimized (vector search)
    processing based on panel selection.
    """
    try:
        if request.panel == "baseline":
            return await process_baseline_query(request.query)
        elif request.panel == "optimized":
            return await process_optimized_query(request.query)
        else:
            raise HTTPException(status_code=400, detail="Invalid panel type")
            
    except Exception as e:
        logger.error(f"Query processing error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/tools")
async def get_all_tools():
    """Get list of all available tools."""
    all_tools = []
    for server_name, tools in TOOLS_CONFIG.items():
        for tool in tools:
            all_tools.append({
                **tool,
                "server": server_name
            })
    return all_tools

@app.delete("/api/cache")
async def clear_cache():
    """Clear the semantic cache."""
    if not is_couchbase_connected:
        return {"cleared_items": 0, "message": "Cache not available - Couchbase not connected"}

    try:
        bucket = COUCHBASE_CONFIG["bucket"]
        scope = COUCHBASE_CONFIG["scope"]
        collection = COUCHBASE_CONFIG["cache_collection"]

        def _clear():
            count_before = 0
            try:
                count_result = cb_cluster.query(f"SELECT RAW COUNT(*) FROM `{bucket}`.`{scope}`.`{collection}`")
                rows = list(count_result.rows())
                count_before = rows[0] if rows else 0
            except Exception:
                pass
            cb_cluster.query(f"DELETE FROM `{bucket}`.`{scope}`.`{collection}`").execute()
            return count_before

        cleared_items = await asyncio.to_thread(_clear)

        logger.info(f"Cache cleared: {cleared_items} items removed")

        return {
            "cleared_items": cleared_items,
            "message": f"Cache cleared! {cleared_items} items removed."
        }

    except Exception as e:
        logger.error(f"Cache clear error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/performance/stats")
async def get_performance_stats():
    """Get comprehensive performance statistics and cache metrics"""
    stats = {
        "config": {
            "semantic_cache_enabled": PERFORMANCE_CONFIG.get("enable_semantic_cache", True),
            "cache_similarity_threshold": PERFORMANCE_CONFIG.get("cache_similarity_threshold", 0.65),
            "log_level": PERFORMANCE_CONFIG.get("log_level", "INFO"),
            "timing_logs_enabled": enable_timing_logs,
            "embedding_cache_size": PERFORMANCE_CONFIG.get("embedding_cache_size", 1000)
        },
        "caches": {
            "tool_lookup_cache_size": len(tool_lookup_cache),
            "couchbase_connected": is_couchbase_connected
        }
    }

    # Add embedding cache stats if available
    if tool_embeddings:
        stats["caches"]["embedding_cache"] = tool_embeddings.get_cache_stats()

    # Add Couchbase cache stats if available
    if is_couchbase_connected:
        try:
            stats["caches"]["couchbase_cache_items"] = await _count_documents(COUCHBASE_CONFIG["cache_collection"])
        except Exception:
            stats["caches"]["couchbase_cache_items"] = "unavailable"

    return stats

@app.get("/api/debug/embeddings")
async def debug_embeddings():
    """Debug endpoint to check embedding text quality."""
    debug_results = []
    
    # Check a few key tools that should match "checkout 5xx errors"
    key_tools = [
        ("datadog", "datadog.search_logs"),
        ("datadog", "datadog.trace_search"), 
        ("datadog", "datadog.analyze_service_performance"),
        ("jira", "jira.get_issue")
    ]
    
    for server_name, tool_name in key_tools:
        if server_name in TOOLS_CONFIG:
            for tool in TOOLS_CONFIG[server_name]:
                if tool["name"] == tool_name:
                    embedding_text = generate_enhanced_embedding_text(tool, server_name)
                    debug_results.append({
                        "tool_name": tool_name,
                        "server": server_name,
                        "embedding_text": embedding_text[:500] + "..." if len(embedding_text) > 500 else embedding_text,
                        "text_length": len(embedding_text)
                    })
                    break
    
    return {"debug_embeddings": debug_results}

@app.post("/api/debug/reindex")
async def force_reindex():
    """Force regeneration of tool embeddings with updated text."""
    if not is_couchbase_connected or not tool_embeddings:
        return {"error": "Couchbase or embeddings not available"}

    try:
        logger.info("FORCE REINDEX: Regenerating all tool embeddings...")

        bucket = COUCHBASE_CONFIG["bucket"]
        scope = COUCHBASE_CONFIG["scope"]

        def _clear_collections():
            try:
                cb_cluster.query(f"DELETE FROM `{bucket}`.`{scope}`.`{COUCHBASE_CONFIG['tools_collection']}`").execute()
                logger.info("Tools collection cleared")
            except Exception as e:
                logger.warning(f"Tools collection clear warning: {e}")
            try:
                cb_cluster.query(f"DELETE FROM `{bucket}`.`{scope}`.`{COUCHBASE_CONFIG['cache_collection']}`").execute()
                logger.info("Cache collection cleared")
            except Exception as e:
                logger.warning(f"Cache collection clear warning: {e}")

        await asyncio.to_thread(_clear_collections)

        # Recreate the Search vector indexes and reindex tools with fresh data
        await ensure_search_indexes()
        await index_tools_with_embeddings()

        return {"message": "Tool embeddings regenerated successfully", "status": "success"}

    except Exception as e:
        logger.error(f"Reindex error: {e}")
        return {"error": str(e), "status": "failed"}

if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host=DEMO_CONFIG["host"],
        port=DEMO_CONFIG["port"],
        reload=DEMO_CONFIG["debug"]
    )