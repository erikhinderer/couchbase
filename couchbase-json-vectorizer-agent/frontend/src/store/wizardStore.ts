import { create } from "zustand";

export interface ClusterFormState {
  label: string;
  connection_string: string;
  username: string;
  password: string;
  is_capella: boolean;
  capella_cluster_id?: string;
  capella_project_id?: string;
  use_tls: boolean;
  bucket_names: string[];
}

const emptyCluster = (label: string, isCapella = false): ClusterFormState => ({
  label,
  connection_string: isCapella ? "couchbases://" : "couchbase://",
  username: "",
  password: "",
  is_capella: isCapella,
  use_tls: isCapella,
  bucket_names: [""],
});

interface WizardState {
  step: number;
  jobName: string;
  source: ClusterFormState;
  destination: ClusterFormState;
  sameServer: boolean;
  embeddingModel: string;
  jobId?: string;
  setStep: (n: number) => void;
  setJobName: (v: string) => void;
  updateSource: (patch: Partial<ClusterFormState>) => void;
  updateDestination: (patch: Partial<ClusterFormState>) => void;
  setSameServer: (v: boolean) => void;
  setEmbeddingModel: (m: string) => void;
  setJobId: (id: string) => void;
  reset: () => void;
}

export const useWizardStore = create<WizardState>((set, get) => ({
  step: 0,
  jobName: "",
  source: emptyCluster("Source Cluster"),
  destination: emptyCluster("Destination Cluster"),
  sameServer: true,
  embeddingModel: "",
  setStep: (n) => set({ step: n }),
  setJobName: (v) => set({ jobName: v }),
  updateSource: (patch) => set((s) => ({ source: { ...s.source, ...patch } })),
  updateDestination: (patch) => set((s) => ({ destination: { ...s.destination, ...patch } })),
  setSameServer: (sameServer) =>
    set((s) => ({
      sameServer,
      // Populate the destination form from the source when the user opts to write
      // embeddings back in place, per the wizard's step-2 checkbox.
      destination: sameServer ? { ...s.source } : s.destination,
    })),
  setEmbeddingModel: (embeddingModel) => set({ embeddingModel }),
  setJobId: (jobId) => set({ jobId }),
  reset: () =>
    set({
      step: 0,
      jobName: "",
      source: emptyCluster("Source Cluster"),
      destination: emptyCluster("Destination Cluster"),
      sameServer: true,
      embeddingModel: "",
      jobId: undefined,
    }),
}));
