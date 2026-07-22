import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useWizardStore } from "@/store/wizardStore";
import StepIndicator from "@/components/wizard/StepIndicator";
import ClusterConfigForm from "@/components/wizard/ClusterConfigForm";
import EmbeddingModelSelector, { EmbeddingModelInfo } from "@/components/wizard/EmbeddingModelSelector";
import ValidationResults from "@/components/validation/ValidationResults";
import { testConnection, listModels, createJob, validateJob, launchJob } from "@/api/client";

export default function SetupWizardPage() {
  const wizard = useWizardStore();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sourceTopo, setSourceTopo] = useState<any>(null);
  const [destTopo, setDestTopo] = useState<any>(null);
  const [models, setModels] = useState<EmbeddingModelInfo[]>([]);
  const [validation, setValidation] = useState<any>(null);

  useEffect(() => {
    listModels().then(setModels).catch(() => {});
  }, []);

  async function guarded(fn: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e: any) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleTestSource() {
    await guarded(async () => setSourceTopo(await testConnection(toApiConfig(wizard.source))));
  }
  async function handleTestDestination() {
    await guarded(async () => setDestTopo(await testConnection(toApiConfig(wizard.destination))));
  }

  async function handleLaunch() {
    await guarded(async () => {
      const plan = {
        name: wizard.jobName || "Untitled vectorizer agent",
        source: toApiConfig(wizard.source),
        destination: toApiConfig(wizard.sameServer ? wizard.source : wizard.destination),
        same_server: wizard.sameServer,
        embedding_model: wizard.embeddingModel,
        vector_field_name: "embedding",
        batch_size: 32,
        poll_interval_seconds: 3,
      };
      const job: any = await createJob(plan);
      wizard.setJobId(job.job_id);
      const validated: any = await validateJob(job.job_id);
      setValidation(validated.validation_report);
      if (!validated.validation_report?.passed) {
        setError("Validation failed -- resolve the issues below and try again.");
        return;
      }
      await launchJob(job.job_id);
      wizard.reset();
      navigate("/");
    });
  }

  const selectedModel = models.find((m) => m.model_id === wizard.embeddingModel);

  return (
    <div style={{ padding: 32, maxWidth: 960 }}>
      <h1 style={{ fontSize: 20, marginBottom: 4 }}>New Vectorizer Agent</h1>
      <p style={{ color: "var(--text-secondary)", fontSize: 13, marginBottom: 28 }}>
        Connect to the Couchbase buckets holding your JSON documents, choose where
        embeddings should be written, pick a model, then launch.
      </p>

      <StepIndicator step={wizard.step} />

      {error && (
        <div className="cb-card" style={{ padding: 12, marginBottom: 16, borderColor: "var(--status-error)" }}>
          <span style={{ color: "var(--status-error)", fontSize: 13 }}>{error}</span>
        </div>
      )}

      {wizard.step === 0 && (
        <StepShell
          title="Agent name & source connection"
          onNext={() => wizard.setStep(1)}
          nextDisabled={!sourceTopo || wizard.source.bucket_names.every((b) => !b.trim())}
        >
          <input
            placeholder="Agent name (e.g. product-catalog-vectorizer)"
            value={wizard.jobName}
            onChange={(e) => wizard.setJobName(e.target.value)}
            style={{ maxWidth: 480, marginBottom: 18 }}
          />
          <ClusterConfigForm
            value={wizard.source}
            onChange={wizard.updateSource}
            bucketFieldLabel="Bucket name(s) containing the JSON documents to vectorize"
          />
          <div style={{ marginTop: 16 }}>
            <button className="cb-btn" onClick={handleTestSource} disabled={busy}>
              Test connection
            </button>
            {sourceTopo && (
              <span className="cb-badge cb-badge-success" style={{ marginLeft: 10 }}>
                Connected · Couchbase Server {sourceTopo.cluster_version} ·{" "}
                {sourceTopo.supports_vector_search ? "Vector Search ready" : "Vector Search NOT available"}
              </span>
            )}
          </div>
        </StepShell>
      )}

      {wizard.step === 1 && (
        <StepShell title="Destination for vectorized documents" onBack={() => wizard.setStep(0)} onNext={() => wizard.setStep(2)} nextDisabled={!wizard.sameServer && !destTopo}>
          <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, marginBottom: 18 }}>
            <input
              type="checkbox"
              checked={wizard.sameServer}
              onChange={(e) => wizard.setSameServer(e.target.checked)}
            />
            Create vector embeddings on the same server containing the JSON documents (write the
            embedding field back into the source buckets, in place)
          </label>

          <ClusterConfigForm
            value={wizard.sameServer ? wizard.source : wizard.destination}
            onChange={wizard.updateDestination}
            disabled={wizard.sameServer}
            bucketFieldLabel="Bucket name(s) to receive vectorized documents"
          />

          {!wizard.sameServer && (
            <div style={{ marginTop: 16 }}>
              <button className="cb-btn" onClick={handleTestDestination} disabled={busy}>
                Test connection
              </button>
              {destTopo && (
                <span className="cb-badge cb-badge-success" style={{ marginLeft: 10 }}>
                  Connected · Couchbase Server {destTopo.cluster_version} ·{" "}
                  {destTopo.supports_vector_search ? "Vector Search ready" : "Vector Search NOT available"}
                </span>
              )}
            </div>
          )}
        </StepShell>
      )}

      {wizard.step === 2 && (
        <StepShell title="Choose an embedding model" onBack={() => wizard.setStep(1)} onNext={() => wizard.setStep(3)} nextDisabled={!wizard.embeddingModel}>
          <p style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 14, maxWidth: 640 }}>
            The 10 most-downloaded text embedding models on Hugging Face, served locally by
            embedding-service. Every JSON document in the configured bucket(s) is embedded with
            this model.
          </p>
          <EmbeddingModelSelector models={models} value={wizard.embeddingModel} onChange={wizard.setEmbeddingModel} />
        </StepShell>
      )}

      {wizard.step === 3 && (
        <StepShell title="Review & launch" onBack={() => wizard.setStep(2)} onNext={handleLaunch} nextDisabled={busy} nextLabel={busy ? "Launching…" : "Launch agent & start vectorizer"}>
          <div className="cb-card" style={{ padding: 16, maxWidth: 640, fontSize: 13, lineHeight: 1.8, marginBottom: 16 }}>
            <div><b>Agent name:</b> {wizard.jobName || "Untitled vectorizer agent"}</div>
            <div><b>Source:</b> {wizard.source.label} ({wizard.source.connection_string}) · buckets: {wizard.source.bucket_names.filter(Boolean).join(", ")}</div>
            <div>
              <b>Destination:</b>{" "}
              {wizard.sameServer
                ? "Same server as source (embeddings written in place)"
                : `${wizard.destination.label} (${wizard.destination.connection_string}) · buckets: ${wizard.destination.bucket_names.filter(Boolean).join(", ")}`}
            </div>
            <div><b>Embedding model:</b> {selectedModel ? `${selectedModel.display_name} (${selectedModel.dimensions} dims)` : wizard.embeddingModel}</div>
          </div>
          <p style={{ fontSize: 12, color: "var(--text-muted)", maxWidth: 640, marginBottom: 16 }}>
            Launching validates connectivity, confirms Couchbase Vector Search is operable
            (Enterprise Edition/Capella, 7.6.0+, FTS enabled), creates the vector search index and
            a supporting pending-document index, then starts backfilling existing documents and
            watching for new ones continuously.
          </p>
          {validation && <ValidationResults checks={validation.checks || []} />}
        </StepShell>
      )}
    </div>
  );
}

function toApiConfig(c: ReturnType<typeof useWizardStore.getState>["source"]) {
  return { ...c, bucket_names: c.bucket_names.filter((b) => b.trim().length > 0) };
}

function StepShell({
  title, children, onBack, onNext, nextDisabled, nextLabel,
}: {
  title: string; children: React.ReactNode; onBack?: () => void; onNext: () => void;
  nextDisabled?: boolean; nextLabel?: string;
}) {
  return (
    <div>
      <h2 style={{ fontSize: 15, marginBottom: 16 }}>{title}</h2>
      {children}
      <div style={{ display: "flex", gap: 10, marginTop: 24 }}>
        {onBack && <button className="cb-btn" onClick={onBack}>Back</button>}
        <button className="cb-btn cb-btn-primary" onClick={onNext} disabled={nextDisabled}>
          {nextLabel ?? "Next"}
        </button>
      </div>
    </div>
  );
}
