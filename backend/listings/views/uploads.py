from __future__ import annotations

import logging
import os
import uuid

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import permissions, serializers as drf_serializers
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import Listing
from ..permissions import IsEmailVerified
from dormcycle.typed import current_user
from typing import Any

logger = logging.getLogger(__name__)

_S3_BUCKET = os.getenv("AWS_S3_BUCKET_NAME", "")
_S3_REGION = os.getenv("AWS_S3_REGION_NAME", "us-east-1")
_UPLOAD_EXPIRY = 300  # presigned URL valid for 5 minutes
_ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
_MAX_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB


def _s3_client() -> Any:
    import boto3
    return boto3.client(
        "s3",
        region_name=_S3_REGION,
        # Set for S3-compatible providers (Cloudflare R2, MinIO); None = AWS.
        endpoint_url=os.getenv("AWS_S3_ENDPOINT_URL") or None,
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    )


@extend_schema(
    operation_id="listing_upload_url",
    request=inline_serializer(
        "UploadUrlRequest",
        fields={
            "content_type": drf_serializers.CharField(),
            "content_length": drf_serializers.IntegerField(),
        },
    ),
    responses={
        200: inline_serializer(
            "UploadUrlResponse",
            fields={
                "upload_url": drf_serializers.CharField(),
                "cdn_url": drf_serializers.CharField(),
                "key": drf_serializers.CharField(),
            },
        )
    },
)
class ListingUploadUrlView(APIView):
    """
    Two-step S3 upload flow:
      1. POST here → get a presigned S3 PUT URL + the final CDN URL
      2. Frontend PUT the file directly to S3
      3. Frontend PATCH /api/listings/:id with { image_url: cdn_url }

    Falls back to a 501 response when S3 is not configured (dev without AWS creds).
    """

    permission_classes = [permissions.IsAuthenticated, IsEmailVerified]

    def post(self, request: Request, pk: int) -> Response:
        if not _S3_BUCKET:
            return Response(
                {"detail": "S3 uploads are not configured on this server."},
                status=501,
            )

        listing = get_object_or_404(
            Listing.objects.filter(owner=current_user(request), is_demo=False),
            pk=pk,
        )
        _ = listing  # ownership confirmed; key scoped to listing

        content_type = request.data.get("content_type", "")
        content_length = request.data.get("content_length", 0)

        if content_type not in _ALLOWED_TYPES:
            return Response(
                {"detail": f"Unsupported content type. Allowed: {', '.join(sorted(_ALLOWED_TYPES))}."},
                status=400,
            )

        try:
            content_length = int(content_length)
        except (TypeError, ValueError):
            return Response({"detail": "content_length must be an integer."}, status=400)

        if content_length <= 0 or content_length > _MAX_SIZE_BYTES:
            return Response(
                {"detail": f"content_length must be between 1 and {_MAX_SIZE_BYTES} bytes."},
                status=400,
            )

        ext = content_type.split("/")[-1].replace("jpeg", "jpg")
        key = f"listing-images/{pk}/{uuid.uuid4().hex}.{ext}"

        try:
            s3 = _s3_client()
            upload_url = s3.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": _S3_BUCKET,
                    "Key": key,
                    "ContentType": content_type,
                    "ContentLength": content_length,
                },
                ExpiresIn=_UPLOAD_EXPIRY,
            )
        except Exception:
            # The boto3 message can name buckets, regions and credentials.
            logger.exception("Presigned upload URL generation failed for listing %s", pk)
            return Response({"detail": "Could not generate an upload URL. Try again shortly."}, status=502)

        from django.conf import settings

        cdn_base = settings.MEDIA_CDN_BASE_URL or f"https://{_S3_BUCKET}.s3.{_S3_REGION}.amazonaws.com"
        cdn_url = f"{cdn_base.rstrip('/')}/{key}"

        return Response({"upload_url": upload_url, "cdn_url": cdn_url, "key": key})
