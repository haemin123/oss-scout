"""Form handler wiring template.

Generates form processing code with React Hook Form and Zod validation.
"""

from __future__ import annotations

from typing import Any


def generate(stack: dict[str, str | None], config: dict[str, Any]) -> dict[str, Any]:
    """Generate form handler wiring code based on detected stack."""
    framework = stack.get("framework")
    fields = config.get("fields", [
        {"name": "name", "type": "string", "required": True},
        {"name": "email", "type": "string", "required": True},
        {"name": "message", "type": "string", "required": False},
    ])
    action_endpoint = config.get("endpoint", "/api/submit")

    if framework in ("nextjs", "react"):
        return _react_hook_form(fields, action_endpoint, framework == "nextjs")
    if stack.get("language") == "python":
        return _python_form(fields, action_endpoint)
    # Default: React Hook Form
    return _react_hook_form(fields, action_endpoint, use_server_action=False)


def _zod_type(field_type: str) -> str:
    """Map field types to Zod validators."""
    mapping = {
        "string": "z.string().min(1)",
        "email": 'z.string().email("Invalid email")',
        "number": "z.number()",
        "url": 'z.string().url("Invalid URL")',
        "boolean": "z.boolean()",
    }
    return mapping.get(field_type, "z.string()")


def _react_hook_form(
    fields: list[dict[str, Any]],
    endpoint: str,
    use_server_action: bool,
) -> dict[str, Any]:
    # Build Zod schema lines
    schema_lines: list[str] = []
    for f in fields:
        zod = _zod_type(f.get("type", "string"))
        if not f.get("required", True):
            zod += ".optional()"
        schema_lines.append(f'  {f["name"]}: {zod},')

    schema_str = "\n".join(schema_lines)

    # Build form field JSX
    field_jsx_parts: list[str] = []
    for f in fields:
        name = f["name"]
        label = f.get("label", name.capitalize())
        field_jsx_parts.append(f'''        <div>
          <label htmlFor="{name}">{label}</label>
          <input
            id="{name}"
            {{...register("{name}")}}
            className="border rounded px-3 py-2 w-full"
          />
          {{errors.{name} && (
            <p className="text-red-500 text-sm">{{errors.{name}?.message}}</p>
          )}}
        </div>''')

    fields_jsx = "\n".join(field_jsx_parts)

    schema_content = f'''import {{ z }} from "zod";

export const formSchema = z.object({{
{schema_str}
}});

export type FormData = z.infer<typeof formSchema>;
'''

    form_content = f'''"use client";

import {{ useForm }} from "react-hook-form";
import {{ zodResolver }} from "@hookform/resolvers/zod";
import {{ formSchema, type FormData }} from "@/lib/schema";

export function ContactForm() {{
  const {{
    register,
    handleSubmit,
    formState: {{ errors, isSubmitting }},
    reset,
  }} = useForm<FormData>({{
    resolver: zodResolver(formSchema),
  }});

  async function onSubmit(data: FormData) {{
    const res = await fetch("{endpoint}", {{
      method: "POST",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify(data),
    }});
    if (!res.ok) throw new Error("Submit failed");
    reset();
  }}

  return (
    <form onSubmit={{handleSubmit(onSubmit)}} className="space-y-4">
{fields_jsx}
      <button
        type="submit"
        disabled={{isSubmitting}}
        className="bg-blue-600 text-white px-4 py-2 rounded disabled:opacity-50"
      >
        {{isSubmitting ? "Submitting..." : "Submit"}}
      </button>
    </form>
  );
}}
'''

    files = [
        {
            "path": "lib/schema.ts",
            "content": schema_content,
            "description": "Zod 폼 검증 스키마",
        },
        {
            "path": "components/ContactForm.tsx",
            "content": form_content,
            "description": "React Hook Form 폼 컴포넌트",
        },
    ]

    return {
        "files": files,
        "usage_example": "import { ContactForm } from '@/components/ContactForm';",
        "dependencies_needed": [
            "react-hook-form",
            "@hookform/resolvers",
            "zod",
        ],
    }


def _python_form(
    fields: list[dict[str, Any]],
    endpoint: str,
) -> dict[str, Any]:
    # Build Pydantic model fields
    pydantic_lines: list[str] = []
    for f in fields:
        py_type = {"string": "str", "email": "EmailStr", "number": "int", "boolean": "bool"}.get(
            f.get("type", "string"), "str"
        )
        if not f.get("required", True):
            py_type = f"{py_type} | None = None"
        pydantic_lines.append(f"    {f['name']}: {py_type}")

    model_fields = "\n".join(pydantic_lines)
    needs_email = any(f.get("type") == "email" for f in fields)
    email_import = "\nfrom pydantic import EmailStr" if needs_email else ""

    content = f'''"""Form submission handler."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel{email_import}

router = APIRouter()


class FormSubmission(BaseModel):
{model_fields}


@router.post("{endpoint}")
async def submit_form(data: FormSubmission) -> dict:
    """Handle form submission."""
    # TODO: Process the form data (save to DB, send email, etc.)
    return {{"success": True, "data": data.model_dump()}}
'''
    return {
        "files": [
            {
                "path": "routes/form.py",
                "content": content,
                "description": "FastAPI 폼 처리 엔드포인트",
            },
        ],
        "usage_example": 'app.include_router(router)',
        "dependencies_needed": ["fastapi", "pydantic[email]"],
    }
