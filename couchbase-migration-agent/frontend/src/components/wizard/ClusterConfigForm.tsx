import type { ClusterFormState } from "@/store/wizardStore";

interface Props {
  value: ClusterFormState;
  onChange: (patch: Partial<ClusterFormState>) => void;
  disableCapellaToggle?: boolean;
  /** Extra content rendered directly below the Password field -- used by the Source
   * step to show the required Couchbase user permissions right where the user is
   * about to type credentials, without hard-coding source-only copy into this
   * shared (source + destination) form component. */
  belowPassword?: React.ReactNode;
}

export default function ClusterConfigForm({ value, onChange, disableCapellaToggle, belowPassword }: Props) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 480 }}>
      <Field label="Friendly name">
        <input value={value.label} onChange={(e) => onChange({ label: e.target.value })} />
      </Field>

      {!disableCapellaToggle && (
        <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13 }}>
          <input
            type="checkbox"
            checked={value.is_capella}
            onChange={(e) =>
              onChange({
                is_capella: e.target.checked,
                use_tls: e.target.checked ? true : value.use_tls,
                connection_string: e.target.checked ? "couchbases://" : "couchbase://",
              })
            }
          />
          This endpoint is a Couchbase Capella cluster
        </label>
      )}

      <Field label={value.is_capella ? "Capella connection string (couchbases://...)" : "Connection string"}>
        <input
          value={value.connection_string}
          onChange={(e) => onChange({ connection_string: e.target.value })}
          placeholder="couchbase://10.0.0.11,10.0.0.12"
        />
      </Field>
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: -8 }}>
        *ensure to add the IP range of the Couchbase Migration Assistant to the source and
        destination Couchbase servers Allowed IP Addresses
      </div>

      {value.is_capella && (
        <>
          <Field label="Capella project ID (optional, enables bucket auto-provisioning)">
            <input
              value={value.capella_project_id ?? ""}
              onChange={(e) => onChange({ capella_project_id: e.target.value })}
            />
          </Field>
          <Field label="Capella cluster ID (optional)">
            <input
              value={value.capella_cluster_id ?? ""}
              onChange={(e) => onChange({ capella_cluster_id: e.target.value })}
            />
          </Field>
        </>
      )}

      <Field label="Username">
        <input value={value.username} onChange={(e) => onChange({ username: e.target.value })} />
      </Field>
      <Field label="Password">
        <input
          type="password"
          value={value.password}
          onChange={(e) => onChange({ password: e.target.value })}
        />
      </Field>
      {belowPassword}

      <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13 }}>
        <input
          type="checkbox"
          checked={value.use_tls}
          disabled={value.is_capella}
          onChange={(e) => onChange({ use_tls: e.target.checked })}
        />
        Use TLS {value.is_capella && "(required for Capella)"}
      </label>

      {!value.is_capella && (
        <label style={{ display: "flex", alignItems: "flex-start", gap: 8, fontSize: 13 }}>
          <input
            type="checkbox"
            checked={value.use_external_network}
            onChange={(e) => onChange({ use_external_network: e.target.checked })}
            style={{ marginTop: 2 }}
          />
          <span>
            Cluster is on a cloud VM or Kubernetes (EC2, GKE, CAO, etc.)
            <br />
            <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
              Enable if backup/restore connects fine at first but then fails partway through
              with "connection refused" (often while transferring GSI index definitions).
              Also requires External IP Address / alternate addressing to be configured on
              the cluster itself (Couchbase Web Console → Server Nodes).
            </span>
          </span>
        </label>
      )}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 12, color: "var(--text-secondary)" }}>
      {label}
      <FieldStyle>{children}</FieldStyle>
    </label>
  );
}

function FieldStyle({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        // apply shared input styling via CSS class on the actual input elements
      }}
    >
      {children}
    </div>
  );
}
