# GET /communities/{communitieId}/posts/{postId}

## Endpoint Information

- **URL:** `https://advanced-api-v2.pump.fun/communities/{communitieId}/posts/{postId}`
- **Method:** `GET`
- **Server:** `https://advanced-api-v2.pump.fun`
- **Operation ID:** `getCommunitiesByCommunitieIdPostsByPostId`
- **Summary:** GET /communities/{communitieId}/posts/{postId}

## Parameters

| Parameter | Type | In | Required | Description |
| --------- | ---- | -- | -------- | ----------- |
| `communitieId` | `string` | path | ✓ |  |
| `postId` | `string` | path | ✓ |  |
| `_rsc` | `string` | query | ✓ |  |

## Responses

### 200

## Example Usage

### cURL

```bash
curl -X GET "https://advanced-api-v2.pump.fun/communities/<communitieId>/posts/<postId>?_rsc=<_rsc>" \
  -H "Authorization: Bearer <your_token>" \
  -H "Accept: application/json"
```

## Notes

- Captured on 2026-06-17 from live pump.fun traffic.
- Replace `<your_token>` and path/query placeholders with real values.
