Welcome to your new TanStack Start app! 

# Getting Started

To run this application:

```bash
npm install
npm run dev
```

---

## Database schema

Canonical tables (singular names, matching `backend/data/db.xlsx`):

| Table | Key columns |
|---|---|
| `product` | `sku`, `title` |
| `supplier` | `name`, `email` (primary resolution address) |
| `purchase_order` | `reference_num`, `supplier_id`, `delivery_date` |
| `purchase_order_line` | `purchase_order_id`, `product_id`, `quantity`, `delivery_date` |
| `supplier_product` | `(supplier_id, product_id)` PK, `supplier_sku`, `price_per_unit` |

Pipeline tables (unchanged): `emails`, `extraction_runs`, `proposed_changes`, `audit_log`, `supplier_email_aliases`.

All canonical tables carry `legacy_id integer` (the integer PK from the xlsx) for seed traceability. These are kept permanently so the xlsx row can always be traced to its uuid.

### Running migrations

```bash
pnpm migrate
```

### Seeding from db.xlsx

```bash
pnpm seed
```

The seed script truncates all canonical tables and re-inserts from `backend/data/db.xlsx`. It also creates one `supplier_email_aliases` row per supplier pointing at `supplier.email`, so alias-based sender resolution works out of the box. Safe to run multiple times in development.

# Building For Production

To build this application for production:

```bash
npm run build
```

## Attachment processing

Image attachments (`image/*` MIME types) are included in the LLM extraction call as base64-encoded vision inputs alongside the email body.

**Limitations:**
- PDF and document attachments (Word, Excel, etc.) are not processed — the LLM only sees their filenames are present, not their contents.
- Attachments are stored at `./attachments/<sha256><ext>` and tracked in the `email_attachments` table. Previously ingested emails will not have `email_attachments` rows; re-ingesting them will populate the table.

## Writeback API

Run `pnpm api` to start the writeback server on port 8002 (configurable via `API_PORT`).

| Method | Path | Body | Action |
|---|---|---|---|
| `POST` | `/proposed-changes/:id/apply` | `{ applied_by?: string }` | Writes the approved change to `purchase_order_line` (version-safe; marks superseded on conflict) |
| `POST` | `/proposed-changes/:id/correct-sku` | `{ correct_product_id: string }` | Records the supplier's SKU mapping and re-points the proposed change at the right line |
| `POST` | `/emails/:id/assign-supplier` | `{ supplier_id: string, retrigger?: boolean }` | Links the sender address to a supplier and, by default, re-runs extract + match on the email |

### Enabling auto-apply

Currently every proposed change lands in `pending` and waits for human review. To enable auto-apply, add a confidence threshold check in `backend/extract.ts` before inserting the row: if the LLM returns a `confidence` score above a chosen threshold (e.g. 0.95), call `applyProposedChange` directly instead of leaving the status as `pending`. Because `applyProposedChange` is already transactional and version-safe, the only change required is that routing decision — no schema or writeback logic changes are needed.

## Testing

This project uses [Vitest](https://vitest.dev/) for testing. You can run the tests with:

```bash
npm run test
```

## Styling

This project uses [Tailwind CSS](https://tailwindcss.com/) for styling.

### Removing Tailwind CSS

If you prefer not to use Tailwind CSS:

1. Remove the demo pages in `src/routes/demo/`
2. Replace the Tailwind import in `src/styles.css` with your own styles
3. Remove `tailwindcss()` from the plugins array in `vite.config.ts`
4. Uninstall the packages: `npm install @tailwindcss/vite tailwindcss -D`

## Linting & Formatting


This project uses [eslint](https://eslint.org/) and [prettier](https://prettier.io/) for linting and formatting. Eslint is configured using [tanstack/eslint-config](https://tanstack.com/config/latest/docs/eslint). The following scripts are available:

```bash
npm run lint
npm run format
npm run check
```



## Routing

This project uses [TanStack Router](https://tanstack.com/router) with file-based routing. Routes are managed as files in `src/routes`.

### Adding A Route

To add a new route to your application just add a new file in the `./src/routes` directory.

TanStack will automatically generate the content of the route file for you.

Now that you have two routes you can use a `Link` component to navigate between them.

### Adding Links

To use SPA (Single Page Application) navigation you will need to import the `Link` component from `@tanstack/react-router`.

```tsx
import { Link } from "@tanstack/react-router";
```

Then anywhere in your JSX you can use it like so:

```tsx
<Link to="/about">About</Link>
```

This will create a link that will navigate to the `/about` route.

More information on the `Link` component can be found in the [Link documentation](https://tanstack.com/router/v1/docs/framework/react/api/router/linkComponent).

### Using A Layout

In the File Based Routing setup the layout is located in `src/routes/__root.tsx`. Anything you add to the root route will appear in all the routes. The route content will appear in the JSX where you render `{children}` in the `shellComponent`.

Here is an example layout that includes a header:

```tsx
import { HeadContent, Scripts, createRootRoute } from '@tanstack/react-router'

export const Route = createRootRoute({
  head: () => ({
    meta: [
      { charSet: 'utf-8' },
      { name: 'viewport', content: 'width=device-width, initial-scale=1' },
      { title: 'My App' },
    ],
  }),
  shellComponent: ({ children }) => (
    <html lang="en">
      <head>
        <HeadContent />
      </head>
      <body>
        <header>
          <nav>
            <Link to="/">Home</Link>
            <Link to="/about">About</Link>
          </nav>
        </header>
        {children}
        <Scripts />
      </body>
    </html>
  ),
})
```

More information on layouts can be found in the [Layouts documentation](https://tanstack.com/router/latest/docs/framework/react/guide/routing-concepts#layouts).

## Server Functions

TanStack Start provides server functions that allow you to write server-side code that seamlessly integrates with your client components.

```tsx
import { createServerFn } from '@tanstack/react-start'

const getServerTime = createServerFn({
  method: 'GET',
}).handler(async () => {
  return new Date().toISOString()
})

// Use in a component
function MyComponent() {
  const [time, setTime] = useState('')
  
  useEffect(() => {
    getServerTime().then(setTime)
  }, [])
  
  return <div>Server time: {time}</div>
}
```

## API Routes

You can create API routes by using the `server` property in your route definitions:

```tsx
import { createFileRoute } from '@tanstack/react-router'
import { json } from '@tanstack/react-start'

export const Route = createFileRoute('/api/hello')({
  server: {
    handlers: {
      GET: () => json({ message: 'Hello, World!' }),
    },
  },
})
```

## Data Fetching

There are multiple ways to fetch data in your application. You can use TanStack Query to fetch data from a server. But you can also use the `loader` functionality built into TanStack Router to load the data for a route before it's rendered.

For example:

```tsx
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/people')({
  loader: async () => {
    const response = await fetch('https://swapi.dev/api/people')
    return response.json()
  },
  component: PeopleComponent,
})

function PeopleComponent() {
  const data = Route.useLoaderData()
  return (
    <ul>
      {data.results.map((person) => (
        <li key={person.name}>{person.name}</li>
      ))}
    </ul>
  )
}
```

Loaders simplify your data fetching logic dramatically. Check out more information in the [Loader documentation](https://tanstack.com/router/latest/docs/framework/react/guide/data-loading#loader-parameters).

# Demo files

Files prefixed with `demo` can be safely deleted. They are there to provide a starting point for you to play around with the features you've installed.

# Learn More

You can learn more about all of the offerings from TanStack in the [TanStack documentation](https://tanstack.com).

For TanStack Start specific documentation, visit [TanStack Start](https://tanstack.com/start).
