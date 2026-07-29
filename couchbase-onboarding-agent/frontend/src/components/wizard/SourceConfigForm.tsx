import type { SourceConnectionConfig, SourceType } from "@/api/types";
import { SOURCE_TYPE_LABELS } from "@/theme/tokens";

const SOURCE_TYPES: SourceType[] = ["mongodb", "dynamodb", "redis", "cassandra", "cosmosdb"];

const PLACEHOLDER: Partial<Record<SourceType, string>> = {
  mongodb: "mongodb://host1,host2/?replicaSet=rs0",
  redis: "redis://host:6379",
  cassandra: "host1,host2,host3",
};

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
      <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-secondary)" }}>{label}</label>
      {children}
    </div>
  );
}

export default function SourceConfigForm({
  value,
  onChange,
}: {
  value: SourceConnectionConfig;
  onChange: (patch: Partial<SourceConnectionConfig>) => void;
}) {
  const t = value.source_type;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <Field label="Source type">
        <select value={t} onChange={(e) => onChange({ source_type: e.target.value as SourceType })}>
          {SOURCE_TYPES.map((v) => (
            <option key={v} value={v}>
              {SOURCE_TYPE_LABELS[v]}
            </option>
          ))}
        </select>
      </Field>

      <Field label="Friendly name">
        <input value={value.label} onChange={(e) => onChange({ label: e.target.value })} placeholder="Production MongoDB" />
      </Field>

      {(t === "mongodb" || t === "redis" || t === "cassandra") && (
        <Field label={t === "cassandra" ? "Contact points (comma-separated)" : "Connection string"}>
          <input
            value={value.connection_string || ""}
            onChange={(e) => onChange({ connection_string: e.target.value })}
            placeholder={PLACEHOLDER[t]}
          />
        </Field>
      )}

      {(t === "mongodb" || t === "cassandra" || t === "cosmosdb") && (
        <Field label={t === "cassandra" ? "Keyspace" : "Database"}>
          <input value={value.database || ""} onChange={(e) => onChange({ database: e.target.value })} />
        </Field>
      )}

      {t === "cassandra" && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          <Field label="Port">
            <input
              type="number"
              value={value.cassandra_port ?? 9042}
              onChange={(e) => onChange({ cassandra_port: Number(e.target.value) })}
            />
          </Field>
          <Field label="Local datacenter">
            <input
              value={value.cassandra_datacenter || ""}
              onChange={(e) => onChange({ cassandra_datacenter: e.target.value })}
              placeholder="datacenter1"
            />
          </Field>
        </div>
      )}

      {t === "redis" && (
        <Field label="Database index">
          <input
            type="number"
            value={value.redis_db_index ?? 0}
            onChange={(e) => onChange({ redis_db_index: Number(e.target.value) })}
          />
        </Field>
      )}

      {(t === "mongodb" || t === "redis" || t === "cassandra") && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          <Field label="Username">
            <input value={value.username || ""} onChange={(e) => onChange({ username: e.target.value })} />
          </Field>
          <Field label="Password">
            <input
              type="password"
              value={value.password || ""}
              onChange={(e) => onChange({ password: e.target.value })}
            />
          </Field>
        </div>
      )}

      {(t === "mongodb" || t === "redis" || t === "cassandra") && (
        <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5 }}>
          <input type="checkbox" checked={!!value.use_tls} onChange={(e) => onChange({ use_tls: e.target.checked })} />
          Use TLS
        </label>
      )}

      {t === "dynamodb" && (
        <>
          <Field label="AWS region">
            <input value={value.aws_region || ""} onChange={(e) => onChange({ aws_region: e.target.value })} placeholder="us-east-1" />
          </Field>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            <Field label="Access key ID">
              <input
                value={value.aws_access_key_id || ""}
                onChange={(e) => onChange({ aws_access_key_id: e.target.value })}
              />
            </Field>
            <Field label="Secret access key">
              <input
                type="password"
                value={value.aws_secret_access_key || ""}
                onChange={(e) => onChange({ aws_secret_access_key: e.target.value })}
              />
            </Field>
          </div>
          <Field label="Session token (optional)">
            <input
              type="password"
              value={value.aws_session_token || ""}
              onChange={(e) => onChange({ aws_session_token: e.target.value })}
            />
          </Field>
          <Field label="Endpoint override (optional -- DynamoDB Local / VPC endpoint)">
            <input
              value={value.dynamodb_endpoint_url || ""}
              onChange={(e) => onChange({ dynamodb_endpoint_url: e.target.value })}
              placeholder="Leave blank for real AWS"
            />
          </Field>
        </>
      )}

      {t === "cosmosdb" && (
        <>
          <Field label="Account endpoint">
            <input
              value={value.cosmos_endpoint || ""}
              onChange={(e) => onChange({ cosmos_endpoint: e.target.value })}
              placeholder="https://my-account.documents.azure.com:443/"
            />
          </Field>
          <Field label="Primary/secondary key">
            <input type="password" value={value.cosmos_key || ""} onChange={(e) => onChange({ cosmos_key: e.target.value })} />
          </Field>
        </>
      )}

      <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: 4 }}>
        Allow-list this agent's IP address on the source database before testing the connection --
        see the README for the exact reachability requirements per source type.
      </div>
    </div>
  );
}
