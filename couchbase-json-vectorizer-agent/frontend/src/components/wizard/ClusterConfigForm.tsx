import { Plus, X } from "lucide-react";
import type { ClusterFormState } from "@/store/wizardStore";

interface Props {
  value: ClusterFormState;
  onChange: (patch: Partial<ClusterFormState>) => void;
  disableCapellaToggle?: boolean;
  disabled?: boolean;
  bucketFieldLabel?: string;
}

/** Connection form fields mirror ClusterConnectionConfig from the Couchbase
 * Migration Agent project (friendly name, connection string, Capella toggle,
 * project/cluster id, credentials, TLS) with one addition: the bucket name(s)
 * holding the JSON documents, entered as a dynamic list with a "+ Add bucket"
 * button. */
export default function ClusterConfigForm({ value, onChange, disableCapellaToggle, disabled, bucketFieldLabel }: Props) {
  function updateBucket(i: number, name: string) {
    const next = [...value.bucket_names];
    next[i] = name;
    onChange({ bucket_names: next });
  }
  function addBucket() {
    onChange({ bucket_names: [...value.bucket_names, ""] });
  }
  function removeBucket(i: number) {
    const next = value.bucket_names.filter((_, idx) => idx !== i);
    onChange({ bucket_names: next.length ? next : [""] });
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 480, opacity: disabled ? 0.6 : 1 }}>
      <Field label="Friendly name">
        <input disabled={disabled} value={value.label} onChange={(e) => onChange({ label: e.target.value })} />
      </Field>

      {!disableCapellaToggle && (
        <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13 }}>
          <input
            type="checkbox"
            disabled={disabled}
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
          disabled={disabled}
          value={value.connection_string}
          onChange={(e) => onChange({ connection_string: e.target.value })}
          placeholder="couchbase://10.0.0.11,10.0.0.12"
        />
      </Field>

      {value.is_capella && (
        <>
          <Field label="Capella project ID (optional)">
            <input
              disabled={disabled}
              value={value.capella_project_id ?? ""}
              onChange={(e) => onChange({ capella_project_id: e.target.value })}
            />
          </Field>
          <Field label="Capella cluster ID (optional)">
            <input
              disabled={disabled}
              value={value.capella_cluster_id ?? ""}
              onChange={(e) => onChange({ capella_cluster_id: e.target.value })}
            />
          </Field>
        </>
      )}

      <Field label="Username">
        <input disabled={disabled} value={value.username} onChange={(e) => onChange({ username: e.target.value })} />
      </Field>
      <Field label="Password">
        <input
          type="password"
          disabled={disabled}
          value={value.password}
          onChange={(e) => onChange({ password: e.target.value })}
        />
      </Field>

      <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13 }}>
        <input
          type="checkbox"
          disabled={disabled || value.is_capella}
          checked={value.use_tls}
          onChange={(e) => onChange({ use_tls: e.target.checked })}
        />
        Use TLS {value.is_capella && "(required for Capella)"}
      </label>

      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>
          {bucketFieldLabel ?? "Bucket name(s) containing the JSON documents"}
        </span>
        {value.bucket_names.map((b, i) => (
          <div key={i} style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <input
              disabled={disabled}
              value={b}
              onChange={(e) => updateBucket(i, e.target.value)}
              placeholder="bucket-name"
            />
            {value.bucket_names.length > 1 && !disabled && (
              <button type="button" className="cb-btn" style={{ padding: 6 }} onClick={() => removeBucket(i)}>
                <X size={13} />
              </button>
            )}
          </div>
        ))}
        {!disabled && (
          <button type="button" className="cb-btn" style={{ alignSelf: "flex-start", padding: "5px 10px", fontSize: 12 }} onClick={addBucket}>
            <Plus size={13} /> Add bucket
          </button>
        )}
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 12, color: "var(--text-secondary)" }}>
      {label}
      {children}
    </label>
  );
}
