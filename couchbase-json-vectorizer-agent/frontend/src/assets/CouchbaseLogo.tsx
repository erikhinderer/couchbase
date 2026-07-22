/**
 * Stylized Couchbase-brand mark: a red droplet/leaf silhouette reminiscent of the
 * Couchbase logo, rendered as inline SVG (no external asset dependency). Not a
 * reproduction of Couchbase's trademarked logo file -- an original mark using the
 * brand's signature red, matching the Couchbase Migration Agent project's asset.
 */
export function CouchbaseMark({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg">
      <circle cx="24" cy="24" r="22" fill="#EA2328" />
      <path
        d="M24 8c6 6.5 10 12.2 10 17.5A10 10 0 1 1 14 25.5C14 20.2 18 14.5 24 8Z"
        fill="#0B0E14"
        opacity="0.9"
      />
      <circle cx="24" cy="27" r="5.5" fill="#EA2328" />
    </svg>
  );
}

export function CouchbaseWordmark() {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
      <CouchbaseMark size={26} />
      <span style={{ fontWeight: 700, fontSize: 15, letterSpacing: "-0.01em", color: "#E8EAED", lineHeight: 1.15 }}>
        Couchbase <span style={{ color: "#EA2328" }}>JSON Vectorizer Agent</span>
      </span>
    </div>
  );
}
