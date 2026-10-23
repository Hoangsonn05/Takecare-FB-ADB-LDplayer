class GoLikeApiError(RuntimeError):
    """Base error for the reusable GoLike client."""


class GoLikeAuthenticationError(GoLikeApiError):
    pass


class GoLikeRateLimitError(GoLikeApiError):
    pass


class GoLikeResponseError(GoLikeApiError):
    pass


class UnsupportedJobError(GoLikeApiError):
    pass


ApiError = GoLikeApiError
AuthenticationError = GoLikeAuthenticationError
RateLimitError = GoLikeRateLimitError
ResponseError = GoLikeResponseError
