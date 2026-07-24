# Narrativa AI Frontend

React + TypeScript frontend for the Presentation Video AI service.

## Development

Start the FastAPI backend from the repository root:

```bash
uvicorn presentation_video.api:app --reload --port 8000
```

In another terminal:

```bash
cd frontend
npm ci
npm run dev
```

Open `http://localhost:5173`. Vite proxies `/v1`, `/health`, `/docs`, and `/openapi.json` to FastAPI.

## Production build

```bash
cd frontend
npm ci
npm run build
cd ..
uvicorn presentation_video.api:app --host 0.0.0.0 --port 8000
```

When `frontend/dist` exists, FastAPI serves the compiled SPA and API from the same origin.

## Structure

```text
src/
├── api/          # Contracts and HTTP gateway adapter
├── components/   # Presentation components
├── hooks/        # Video creation and polling use case
├── utils/        # Formatting helpers
├── App.tsx
└── styles.css
```

The UI depends on the `VideoGateway` interface rather than `fetch` directly. Another transport, mock, BFF, or generated API client can replace `HttpVideoGateway` without changing the components.
