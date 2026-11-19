/** The Worker entry. workerd treats every export of this module as a handler, so the
 * app and its helpers live in ``app.ts`` and only the handler is exported here. */

import { handle, type Context, type Env } from "./app";

export default {
  fetch: (request: Request, env: Env, ctx: Context) => handle(request, env, ctx),
};
