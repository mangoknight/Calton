import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import type { CaltonError } from '@/api/errors';
import type { Paginated } from '@/api/pagination';
import {
	createToken,
	deleteToken,
	getTokens,
	type APIToken,
	type CreateTokenPayload,
	type CreatedToken,
} from '@/api/tokens';

export const tokenKeys = {
	all: ['tokens'] as const,
};

export function useTokens() {
	return useQuery<Paginated<APIToken>, CaltonError>({
		queryKey: tokenKeys.all,
		queryFn: () => getTokens(),
	});
}

export function useCreateToken() {
	const queryClient = useQueryClient();

	return useMutation<CreatedToken, CaltonError, CreateTokenPayload>({
		mutationFn: (payload) => createToken(payload),
		onSuccess: () => {
			void queryClient.invalidateQueries({ queryKey: tokenKeys.all });
		},
	});
}

export function useDeleteToken() {
	const queryClient = useQueryClient();

	return useMutation<{ message: string }, CaltonError, number>({
		mutationFn: (tokenId) => deleteToken(tokenId),
		onSuccess: () => {
			void queryClient.invalidateQueries({ queryKey: tokenKeys.all });
		},
	});
}
