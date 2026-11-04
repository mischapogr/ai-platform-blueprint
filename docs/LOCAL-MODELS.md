# Local models and semantic evaluation

The provider boundary supports offline lexical hashing, OpenAI Responses/embeddings
and native Ollama chat/embeddings. Ollama is optional; default startup and CI still
require no model runtime, downloads, API key or paid call.

## Optional Compose runtime

After `make init`, prepare the CPU runtime and its model volume explicitly:

```bash
PLATFORM_LOCAL_MODELS=1 ./scripts/compose.sh up --detach --build ollama
PLATFORM_LOCAL_MODELS=1 ./scripts/compose.sh exec -T ollama ollama pull nomic-embed-text:latest
PLATFORM_LOCAL_MODELS=1 ./scripts/compose.sh exec -T ollama ollama pull llama3.2:3b
PLATFORM_LOCAL_MODELS=1 make up
```

The first pulls can download several gigabytes. Weights stay in the project's
`ollama_models` Docker volume. Check model licenses before redistributing weights;
the repository license does not relicense them. The runtime adds a 6 GiB memory
limit and four CPUs, with no GPU requirement or published model port. Its writable
paths are the model volume and temporary storage; it runs as UID 10001 with dropped
capabilities. It has egress for explicit model downloads, with Ollama cloud features
disabled. The API and worker reach it through the internal `models` network.

Both optional features can be selected together:

```bash
PLATFORM_LOCAL_MODELS=1 PLATFORM_TRACING=1 make up
```

Use the same flags for subsequent Compose operations. `make down` preserves model
and application volumes when invoked with the deployment's flags.

In the browser, select **Ollama semantic embeddings** when creating a collection.
Select **Ollama · local model** in the composer for generated answers. A local-only
collection permits this path while continuing to reject OpenAI processing. An
OpenAI embedding collection still requires external consent for its retrieval,
regardless of which answer provider is selected. General chat attaches no collection.

## Identity and limits

The operator configures `OLLAMA_BASE_URL`, `OLLAMA_CHAT_MODELS`,
`OLLAMA_EMBEDDING_MODEL` and `OLLAMA_EMBEDDING_PROFILE`; the browser cannot supply
an arbitrary endpoint. Allowed endpoints are explicit HTTP ports on loopback,
`localhost` or the Compose service name `ollama`. Proxies and redirects are disabled.
There is no silent provider fallback. An unavailable model fails with a bounded,
content-free error instead of sending data to an external service.

A local embedding collection records the model name, installed manifest digest,
native dimensions, endpoint and preprocessing profile. Each embedding operation
checks that identity against current configuration, with a second digest check
after inference. Generation also checks for a digest change during the call.
Changing the embedding configuration requires creating
another collection and reimporting. The default `nomic-v1` profile prefixes documents
with `search_document: ` and queries with `search_query: `; `plain` is available for
models with another documented input convention. Embeddings use `truncate=false`,
so overlong inputs fail instead of being silently shortened. Vectors must have the
expected count/dimensions, contain finite numeric values and be nonzero. Local
vectors are L2-normalized before storage to avoid float-range/underflow problems.
These checks assume an operator-managed runtime; they are not remote attestation
against a malicious model server or a proof against a tag changed and restored
between checks.

Generation is non-streaming and stateless, without tools. It uses temperature zero,
a fixed seed, up to 192 output tokens, four CPU inference threads and zero GPU
layers. Its context setting is capped at the model's declared capacity and 8,192
tokens. A conservative UTF-8 byte budget rejects oversized prompts/history before
inference. This may reject inputs that a tokenizer could fit; it avoids quietly
truncating source evidence. The HTTP client has a 60-second I/O timeout and a short
connect timeout, not a guaranteed total wall-clock deadline across all requests.
The ingestion supervisor retains its independent 180-second child timeout.

Reported token usage comes from the runtime response. Missing counts remain null
and are shown as unreported; they are not invented as zero. Usage sums cover
reported tokens only and do not establish full consumption when some calls omit
usage. Local counts are not external API billing or a hardware-cost estimate.

## Opt-in native qualification

With the project's Python environment, Docker and the Ollama binary installed:

```bash
make local-model-test  # provider API/worker flow, semantic comparison and answer evaluation
make semantic-eval    # provider flow plus the fixed retrieval comparison
make answer-eval      # provider flow plus fixed/retrieved answer evaluation
```

These commands start a separate native Ollama server on a fresh loopback port and
use `.local/models/qualification` as a project-private cache. They do not change
models or settings in an existing Ollama service. A new temporary PostgreSQL
container publishes only a random loopback port and has no existing volumes or
workspace credentials. The test creates synthetic collections, exercises the real
worker child and authenticated API, and verifies that a local-only collection
rejects external generation. OpenAI credentials are not available to the run.

The owned server process and database container are removed afterward. The model
cache and ignored `.local/evidence/ai-blueprint-models-*` reports remain for reuse
and diagnosis. Native qualification does not itself qualify the optional Docker
runtime's deployment on another machine. The backend and evaluation inputs are hashed before
and after execution; editing them during a run invalidates its evidence.

The semantic comparison uses the unchanged 48-question B01 corpus, ranking cutoff,
chunking and metric definitions. Each provider uses its own collections/index.
The offline baseline remains gated; semantic results are reported separately and
do not replace it. Reports include model digests, dimensions, profiles, runtime
version, CPU configuration, sampled process-tree resident memory, per-question
retrieval times and reported usage. Memory samples sum the owned server and runner
processes; they are approximate and can double-count shared pages. They do not
establish a production memory or throughput guarantee.

See [retrieval evaluation](RETRIEVAL-EVALUATION.md),
[answer evaluation](ANSWER-EVALUATION.md) and the
[native Ollama API reference](https://github.com/ollama/ollama/blob/main/docs/api.md).

## Optional VM CPU profile

The local-lab profile can run a source-built CPU-only Ollama service on the VM's
private backend network. It has no published port, cloud access, GPU devices or
application/database secrets. Only the API/worker call it. Models are copied from
the project-owned qualification cache into a read-only guest mount; collection
metadata persists the embedding digest/profile/dimensions as before.

```bash
make lab-model-enable LAB_VM=recovery
make lab-model-test LAB_VM=recovery
```

Enablement transfers only the model blobs/manifests, takes an encrypted database
backup, cleanly stops the owned guest services and reboots the **same disk** with
8 GiB RAM and 4 vCPUs. It then deploys the locally signed image envelope. The model
container has a 4.5 GiB limit, two CPUs, one loaded model, one parallel request,
a bounded queue and no model keep-alive. CPU generation has a bounded 180-second
read timeout; this is an interactive lab, not a throughput qualification.

The profile uses `nomic-embed-text:latest` for 768-dimensional embeddings and
`llama3.2:3b` for generation, with actual digests recorded during qualification.
Existing offline collections retain their original vectors. To use semantic
retrieval, create a **new collection with Ollama embeddings**, add your sources,
wait until they are ready, then select the local model in chat. Existing lexical
collections can also use local source-excerpt answers without changing their
stored embeddings. No external provider key is needed.

Knowledge mode returns validated model-selected source excerpts and application-
rendered citations, or an insufficient-evidence response. It does not freely
paraphrase source facts. General chat still generates free-form text and has no
knowledge-grounding claim. See [answer evaluation](ANSWER-EVALUATION.md).

The VM check creates/deletes only its own synthetic collection. It exercises
worker semantic indexing, local answers, reported usage, unsupported-question
abstention, citation bounds, a source-instruction canary and external-provider
refusal. Model health appears through the guest resource sampler and a dedicated
alert; actual inference needs the workflow check as well as a running process.

The guest CPU profile sets `OLLAMA_NOPRUNE=1` because its seeded model cache is
read-only. Ollama 0.40.14 otherwise prunes empty manifest directories at startup,
which can fail on a subsequent boot. Models remain immutable; updates require
explicit cache qualification and transfer.
