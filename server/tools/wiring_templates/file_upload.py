"""File upload wiring template.

Generates file upload handlers for Firebase Storage, S3, and local (multer).
"""

from __future__ import annotations

from typing import Any


def generate(stack: dict[str, str | None], config: dict[str, Any]) -> dict[str, Any]:
    """Generate file upload wiring code based on detected stack."""
    storage = stack.get("storage")
    max_size = config.get("max_size_mb", 10)
    allowed_types = config.get("allowed_types", ["image/jpeg", "image/png", "image/webp"])

    if storage == "firebase-storage":
        return _firebase_storage(max_size, allowed_types)
    if storage in ("s3", "cloudinary"):
        return _presigned_url(max_size)
    if stack.get("framework") == "express":
        return _multer_local(max_size, allowed_types)
    # Default: Firebase Storage
    return _firebase_storage(max_size, allowed_types)


def _firebase_storage(
    max_size: int,
    allowed_types: list[str],
) -> dict[str, Any]:
    types_str = ", ".join(f'"{t}"' for t in allowed_types)
    content = f'''import {{ ref, uploadBytesResumable, getDownloadURL }} from "firebase/storage";
import {{ storage }} from "@/lib/firebase";

const MAX_SIZE = {max_size} * 1024 * 1024; // {max_size}MB
const ALLOWED_TYPES = [{types_str}];

interface UploadResult {{
  url: string;
  path: string;
}}

interface UploadProgress {{
  progress: number;
  state: "running" | "paused" | "success" | "error";
}}

export async function uploadFile(
  file: File,
  path: string,
  onProgress?: (p: UploadProgress) => void,
): Promise<UploadResult> {{
  if (file.size > MAX_SIZE) {{
    throw new Error(`File size exceeds {max_size}MB limit`);
  }}
  if (!ALLOWED_TYPES.includes(file.type)) {{
    throw new Error(`File type ${{file.type}} is not allowed`);
  }}

  const storageRef = ref(storage, `${{path}}/${{Date.now()}}_${{file.name}}`);
  const uploadTask = uploadBytesResumable(storageRef, file);

  return new Promise((resolve, reject) => {{
    uploadTask.on(
      "state_changed",
      (snapshot) => {{
        const progress = (snapshot.bytesTransferred / snapshot.totalBytes) * 100;
        onProgress?.({{ progress, state: snapshot.state as UploadProgress["state"] }});
      }},
      (error) => reject(error),
      async () => {{
        const url = await getDownloadURL(uploadTask.snapshot.ref);
        resolve({{ url, path: storageRef.fullPath }});
      }},
    );
  }});
}}
'''
    return {
        "files": [
            {
                "path": "lib/upload.ts",
                "content": content,
                "description": "Firebase Storage 파일 업로드 함수",
            },
        ],
        "usage_example": (
            'const result = await uploadFile(file, "uploads", (p) => console.log(p.progress));'
        ),
        "dependencies_needed": ["firebase"],
    }


def _presigned_url(max_size: int) -> dict[str, Any]:
    api_content = f'''import {{ NextRequest, NextResponse }} from "next/server";
import {{ S3Client, PutObjectCommand }} from "@aws-sdk/client-s3";
import {{ getSignedUrl }} from "@aws-sdk/s3-request-presigner";

const s3 = new S3Client({{
  region: process.env.AWS_REGION ?? "us-east-1",
  credentials: {{
    accessKeyId: process.env.AWS_ACCESS_KEY_ID ?? "",
    secretAccessKey: process.env.AWS_SECRET_ACCESS_KEY ?? "",
  }},
}});

const BUCKET = process.env.S3_BUCKET ?? "";
const MAX_SIZE = {max_size} * 1024 * 1024;

export async function POST(request: NextRequest) {{
  const {{ filename, contentType }} = await request.json();
  if (!filename || !contentType) {{
    return NextResponse.json({{ error: "filename and contentType required" }}, {{ status: 400 }});
  }}

  const key = `uploads/${{Date.now()}}_${{filename}}`;
  const command = new PutObjectCommand({{
    Bucket: BUCKET,
    Key: key,
    ContentType: contentType,
    ContentLength: MAX_SIZE,
  }});

  const presignedUrl = await getSignedUrl(s3, command, {{ expiresIn: 3600 }});

  return NextResponse.json({{ presignedUrl, key }});
}}
'''

    client_content = '''export async function uploadWithPresignedUrl(file: File): Promise<string> {
  // Step 1: Get presigned URL
  const res = await fetch("/api/upload", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename: file.name, contentType: file.type }),
  });
  const { presignedUrl, key } = await res.json();

  // Step 2: Upload directly to S3
  await fetch(presignedUrl, {
    method: "PUT",
    headers: { "Content-Type": file.type },
    body: file,
  });

  return key;
}
'''
    return {
        "files": [
            {
                "path": "app/api/upload/route.ts",
                "content": api_content,
                "description": "S3 Presigned URL 생성 API 라우트",
            },
            {
                "path": "lib/upload.ts",
                "content": client_content,
                "description": "Presigned URL 기반 업로드 클라이언트",
            },
        ],
        "usage_example": 'const key = await uploadWithPresignedUrl(file);',
        "dependencies_needed": [
            "@aws-sdk/client-s3",
            "@aws-sdk/s3-request-presigner",
        ],
    }


def _multer_local(max_size: int, allowed_types: list[str]) -> dict[str, Any]:
    mimes_str = ", ".join(f'"{t}"' for t in allowed_types)
    content = f'''import multer from "multer";
import path from "path";
import {{ Request }} from "express";

const UPLOAD_DIR = "uploads";
const MAX_SIZE = {max_size} * 1024 * 1024; // {max_size}MB
const ALLOWED_MIMES = [{mimes_str}];

const storage = multer.diskStorage({{
  destination: (_req, _file, cb) => {{
    cb(null, UPLOAD_DIR);
  }},
  filename: (_req, file, cb) => {{
    const uniqueSuffix = `${{Date.now()}}-${{Math.round(Math.random() * 1e9)}}`;
    cb(null, `${{uniqueSuffix}}${{path.extname(file.originalname)}}`);
  }},
}});

function fileFilter(_req: Request, file: Express.Multer.File, cb: multer.FileFilterCallback) {{
  if (ALLOWED_MIMES.includes(file.mimetype)) {{
    cb(null, true);
  }} else {{
    cb(new Error(`File type ${{file.mimetype}} is not allowed`));
  }}
}}

export const upload = multer({{
  storage,
  limits: {{ fileSize: MAX_SIZE }},
  fileFilter,
}});
'''
    return {
        "files": [
            {
                "path": "middleware/upload.ts",
                "content": content,
                "description": "Multer 파일 업로드 미들웨어",
            },
        ],
        "usage_example": 'app.post("/upload", upload.single("file"), handler);',
        "dependencies_needed": ["multer", "@types/multer"],
    }
