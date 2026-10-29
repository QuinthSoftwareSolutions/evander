using Microsoft.Graph.Communications.Client.Authentication;
using Microsoft.IdentityModel.JsonWebTokens;
using Microsoft.IdentityModel.Tokens;

namespace TeamsMeetingMediaBridge;

public static class InboundCallTokenValidator
{
    public static async Task<RequestValidationResult> ValidateAsync(string token,
        IEnumerable<SecurityKey> signingKeys, string appId, string allowedTenant)
    {
        var handler = new JsonWebTokenHandler();
        var result = await handler.ValidateTokenAsync(token, new TokenValidationParameters
        {
            RequireSignedTokens = true,
            ValidateIssuerSigningKey = true,
            IssuerSigningKeys = signingKeys,
            ValidateIssuer = true,
            ValidIssuers = ["https://graph.microsoft.com", "https://api.botframework.com"],
            ValidateAudience = true,
            ValidAudience = appId,
            ValidateLifetime = true,
            ClockSkew = TimeSpan.FromMinutes(2),
        });
        if (!result.IsValid) return new RequestValidationResult { IsValid = false, TenantId = "" };
        var claims = handler.ReadJsonWebToken(token).Claims.ToArray();
        var tenants = claims.Where(claim => claim.Type is "tid" or "http://schemas.microsoft.com/identity/claims/tenantid")
            .Select(claim => claim.Value).ToArray();
        var valid = tenants.Length > 0 && tenants.All(tenant =>
            string.Equals(tenant, allowedTenant, StringComparison.OrdinalIgnoreCase));
        return new RequestValidationResult { IsValid = valid, TenantId = valid ? allowedTenant : "" };
    }
}
