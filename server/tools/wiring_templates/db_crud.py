"""DB CRUD wiring template.

Generates database CRUD hooks/functions for Firestore, Prisma, Supabase, and MongoDB.
"""

from __future__ import annotations

from typing import Any


def generate(stack: dict[str, str | None], config: dict[str, Any]) -> dict[str, Any]:
    """Generate DB CRUD wiring code based on detected stack."""
    db = stack.get("db")
    collection = config.get("collection", "items")
    model_name = config.get("model", collection.rstrip("s").capitalize())

    if db == "firestore":
        return _firestore_hooks(collection)
    if db == "prisma":
        return _prisma_crud(model_name)
    if db == "supabase":
        return _supabase_hooks(collection)
    if db in ("mongodb", "mongoose"):
        return _mongoose_crud(collection, model_name)
    # Default: Firestore (common for OSS Scout users)
    return _firestore_hooks(collection)


def _firestore_hooks(collection: str) -> dict[str, Any]:
    hook_content = f'''import {{ useState, useEffect, useCallback }} from "react";
import {{
  collection as firestoreCollection,
  doc,
  getDocs,
  getDoc,
  addDoc,
  updateDoc,
  deleteDoc,
  query,
  orderBy,
  onSnapshot,
  type DocumentData,
  type QueryConstraint,
}} from "firebase/firestore";
import {{ db }} from "@/lib/firebase";

const COLLECTION = "{collection}";

export function useCollection<T extends DocumentData>(
  constraints: QueryConstraint[] = [],
  realtime = false,
) {{
  const [data, setData] = useState<(T & {{ id: string }})[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {{
    const ref = firestoreCollection(db, COLLECTION);
    const q = query(ref, ...constraints);

    if (realtime) {{
      const unsubscribe = onSnapshot(
        q,
        (snapshot) => {{
          setData(
            snapshot.docs.map((d) => ({{ id: d.id, ...d.data() }} as T & {{ id: string }})),
          );
          setLoading(false);
        }},
        (err) => {{
          setError(err.message);
          setLoading(false);
        }},
      );
      return unsubscribe;
    }}

    getDocs(q)
      .then((snapshot) => {{
        setData(
          snapshot.docs.map((d) => ({{ id: d.id, ...d.data() }} as T & {{ id: string }})),
        );
      }})
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }}, []);  // eslint-disable-line react-hooks/exhaustive-deps

  return {{ data, loading, error }};
}}

export function useDocument<T extends DocumentData>(docId: string | null) {{
  const [data, setData] = useState<(T & {{ id: string }}) | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {{
    if (!docId) {{
      setLoading(false);
      return;
    }}
    const ref = doc(db, COLLECTION, docId);
    getDoc(ref)
      .then((snap) => {{
        if (snap.exists()) {{
          setData({{ id: snap.id, ...snap.data() }} as T & {{ id: string }});
        }}
      }})
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }}, [docId]);

  return {{ data, loading, error }};
}}

export function useCrud() {{
  const create = useCallback(async (data: DocumentData) => {{
    const ref = firestoreCollection(db, COLLECTION);
    const docRef = await addDoc(ref, {{ ...data, createdAt: new Date() }});
    return docRef.id;
  }}, []);

  const update = useCallback(async (id: string, data: Partial<DocumentData>) => {{
    const ref = doc(db, COLLECTION, id);
    await updateDoc(ref, {{ ...data, updatedAt: new Date() }});
  }}, []);

  const remove = useCallback(async (id: string) => {{
    const ref = doc(db, COLLECTION, id);
    await deleteDoc(ref);
  }}, []);

  return {{ create, update, remove }};
}}
'''
    return {
        "files": [
            {
                "path": f"hooks/use{collection.capitalize()}.ts",
                "content": hook_content,
                "description": f"Firestore {collection} 컬렉션 CRUD 훅",
            },
        ],
        "usage_example": (
            f"const {{ data, loading }} = useCollection();\n"
            f"const {{ create, update, remove }} = useCrud();"
        ),
        "dependencies_needed": ["firebase"],
    }


def _prisma_crud(model_name: str) -> dict[str, Any]:
    lower = model_name.lower()
    content = f'''import {{ NextRequest, NextResponse }} from "next/server";
import {{ prisma }} from "@/lib/prisma";

// GET /api/{lower}s
export async function GET(request: NextRequest) {{
  const searchParams = request.nextUrl.searchParams;
  const page = parseInt(searchParams.get("page") ?? "1", 10);
  const limit = parseInt(searchParams.get("limit") ?? "20", 10);
  const skip = (page - 1) * limit;

  const [items, total] = await Promise.all([
    prisma.{lower}.findMany({{ skip, take: limit, orderBy: {{ createdAt: "desc" }} }}),
    prisma.{lower}.count(),
  ]);

  return NextResponse.json({{
    success: true,
    data: items,
    meta: {{ page, limit, total, totalPages: Math.ceil(total / limit) }},
  }});
}}

// POST /api/{lower}s
export async function POST(request: NextRequest) {{
  const body = await request.json();
  const item = await prisma.{lower}.create({{ data: body }});
  return NextResponse.json({{ success: true, data: item }}, {{ status: 201 }});
}}
'''

    detail_content = f'''import {{ NextRequest, NextResponse }} from "next/server";
import {{ prisma }} from "@/lib/prisma";

interface Params {{
  params: {{ id: string }};
}}

// GET /api/{lower}s/:id
export async function GET(_request: NextRequest, {{ params }}: Params) {{
  const item = await prisma.{lower}.findUnique({{ where: {{ id: params.id }} }});
  if (!item) {{
    return NextResponse.json({{ success: false, error: "Not found" }}, {{ status: 404 }});
  }}
  return NextResponse.json({{ success: true, data: item }});
}}

// PUT /api/{lower}s/:id
export async function PUT(request: NextRequest, {{ params }}: Params) {{
  const body = await request.json();
  const item = await prisma.{lower}.update({{
    where: {{ id: params.id }},
    data: body,
  }});
  return NextResponse.json({{ success: true, data: item }});
}}

// DELETE /api/{lower}s/:id
export async function DELETE(_request: NextRequest, {{ params }}: Params) {{
  await prisma.{lower}.delete({{ where: {{ id: params.id }} }});
  return NextResponse.json({{ success: true }});
}}
'''
    return {
        "files": [
            {
                "path": f"app/api/{lower}s/route.ts",
                "content": content,
                "description": f"Prisma {model_name} 목록/생성 API 라우트",
            },
            {
                "path": f"app/api/{lower}s/[id]/route.ts",
                "content": detail_content,
                "description": f"Prisma {model_name} 상세/수정/삭제 API 라우트",
            },
        ],
        "usage_example": (
            f"// GET /api/{lower}s?page=1&limit=20\n"
            f'// POST /api/{lower}s {{ "name": "..." }}'
        ),
        "dependencies_needed": ["@prisma/client", "prisma"],
    }


def _supabase_hooks(collection: str) -> dict[str, Any]:
    content = f'''import {{ useState, useEffect, useCallback }} from "react";
import {{ supabase }} from "@/lib/supabase";

const TABLE = "{collection}";

export function useSupabaseQuery<T>(
  select = "*",
  filters?: Record<string, unknown>,
) {{
  const [data, setData] = useState<T[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {{
    let query = supabase.from(TABLE).select(select);
    if (filters) {{
      for (const [key, value] of Object.entries(filters)) {{
        query = query.eq(key, value);
      }}
    }}

    query
      .then(({{ data: result, error: err }}) => {{
        if (err) throw err;
        setData((result ?? []) as T[]);
      }})
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }}, []);  // eslint-disable-line react-hooks/exhaustive-deps

  return {{ data, loading, error }};
}}

export function useSupabaseCrud<T>() {{
  const create = useCallback(async (data: Partial<T>) => {{
    const {{ data: result, error }} = await supabase.from(TABLE).insert(data).select().single();
    if (error) throw error;
    return result as T;
  }}, []);

  const update = useCallback(async (id: string, data: Partial<T>) => {{
    const {{ data: result, error }} = await supabase
      .from(TABLE)
      .update(data)
      .eq("id", id)
      .select()
      .single();
    if (error) throw error;
    return result as T;
  }}, []);

  const remove = useCallback(async (id: string) => {{
    const {{ error }} = await supabase.from(TABLE).delete().eq("id", id);
    if (error) throw error;
  }}, []);

  return {{ create, update, remove }};
}}
'''
    return {
        "files": [
            {
                "path": f"hooks/use{collection.capitalize()}.ts",
                "content": content,
                "description": f"Supabase {collection} 테이블 CRUD 훅",
            },
        ],
        "usage_example": (
            f'const {{ data, loading }} = useSupabaseQuery("{collection}");\n'
            f"const {{ create, update, remove }} = useSupabaseCrud();"
        ),
        "dependencies_needed": ["@supabase/supabase-js"],
    }


def _mongoose_crud(collection: str, model_name: str) -> dict[str, Any]:
    model_content = f'''import mongoose, {{ Schema, type Document }} from "mongoose";

export interface I{model_name} extends Document {{
  name: string;
  createdAt: Date;
  updatedAt: Date;
}}

const {model_name}Schema = new Schema<I{model_name}>(
  {{
    name: {{ type: String, required: true }},
  }},
  {{ timestamps: true }},
);

export const {model_name} =
  mongoose.models.{model_name} ??
  mongoose.model<I{model_name}>("{model_name}", {model_name}Schema);
'''

    controller_content = f'''import {{ Request, Response }} from "express";
import {{ {model_name} }} from "@/models/{model_name}";

export async function getAll(req: Request, res: Response) {{
  const page = parseInt(req.query.page as string) || 1;
  const limit = parseInt(req.query.limit as string) || 20;
  const skip = (page - 1) * limit;

  const [items, total] = await Promise.all([
    {model_name}.find().skip(skip).limit(limit).sort({{ createdAt: -1 }}),
    {model_name}.countDocuments(),
  ]);

  res.json({{
    success: true,
    data: items,
    meta: {{ page, limit, total, totalPages: Math.ceil(total / limit) }},
  }});
}}

export async function getById(req: Request, res: Response) {{
  const item = await {model_name}.findById(req.params.id);
  if (!item) {{
    res.status(404).json({{ success: false, error: "Not found" }});
    return;
  }}
  res.json({{ success: true, data: item }});
}}

export async function create(req: Request, res: Response) {{
  const item = await {model_name}.create(req.body);
  res.status(201).json({{ success: true, data: item }});
}}

export async function update(req: Request, res: Response) {{
  const item = await {model_name}.findByIdAndUpdate(req.params.id, req.body, {{ new: true }});
  if (!item) {{
    res.status(404).json({{ success: false, error: "Not found" }});
    return;
  }}
  res.json({{ success: true, data: item }});
}}

export async function remove(req: Request, res: Response) {{
  await {model_name}.findByIdAndDelete(req.params.id);
  res.json({{ success: true }});
}}
'''
    return {
        "files": [
            {
                "path": f"models/{model_name}.ts",
                "content": model_content,
                "description": f"Mongoose {model_name} 모델",
            },
            {
                "path": f"controllers/{model_name}Controller.ts",
                "content": controller_content,
                "description": f"Mongoose {model_name} CRUD 컨트롤러",
            },
        ],
        "usage_example": (
            f'router.get("/{collection}", getAll);\n'
            f'router.post("/{collection}", create);'
        ),
        "dependencies_needed": ["mongoose"],
    }
