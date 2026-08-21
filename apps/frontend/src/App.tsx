// App shell: logged out shows the login view, logged in shows the app with the tenant badge in the header.

import { useSyncExternalStore } from "react";

import { getSession, subscribe } from "./auth";
import { AppLayout } from "./components/layout";
import { ChatView } from "./views/ChatView";
import { LoginView } from "./views/LoginView";
import { SessionBadge } from "./views/SessionBadge";

export default function App() {
  const session = useSyncExternalStore(subscribe, getSession, getSession);

  if (!session) {
    return (
      <AppLayout>
        <LoginView />
      </AppLayout>
    );
  }

  return (
    <AppLayout tenantBadge={<SessionBadge session={session} />}>
      <ChatView />
    </AppLayout>
  );
}
