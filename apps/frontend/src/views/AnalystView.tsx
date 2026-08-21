// The authenticated page: the chat views of the remaining M4 issues grow in here.

import { EmptyState, Page, PageHeader, Section } from "../components/layout";
import { API_BASE_URL } from "../config";

export function AnalystView() {
  return (
    <Page className="section-stack">
      <PageHeader
        eyebrow="secure-rls"
        title="Conversational data analyst"
        subtitle="Ask questions about your tenant's HR data. Row-level security is enforced server-side, so an answer can never cross a tenant boundary."
      />

      <Section title="Backend">
        <div className="settings-row">
          <div className="settings-label">
            <div className="settings-name">API base URL</div>
            <div className="settings-help">Set VITE_API_URL to point this SPA at another backend.</div>
          </div>
          <div className="settings-control">
            <span className="mono-inline">{API_BASE_URL}</span>
          </div>
        </div>
      </Section>

      <Section title="Chat">
        <EmptyState icon="message-circle">
          Streaming chat, the SQL trace and the conversation history arrive with the remaining M4
          issues.
        </EmptyState>
      </Section>
    </Page>
  );
}
